"""
solver.py – Capacitated Vehicle Routing with Time Windows and multiple trips
(multi-trip CVRPTW) using Google OR-Tools.

Model
-----
* nodes[0] = depot, then one node per order, then optional *reload* nodes
  (located at the depot). Visiting a reload node resets the vehicle's load, so
  one vehicle can do "trip 1 → reload at Koyambedu → trip 2".
* Dimensions
    Load  – kg delivered since last (re)load  ≤ vehicle capacity
    Time  – seconds since planned departure, waiting allowed, ≤ horizon
    Dist  – metres (reporting)
* Objective (cost units are 0.01 s or 0.01 m):
    arc cost  (travel)                       weight 100
    + per-vehicle span (drive+service+wait)  weight  50  → waiting costs ~1/3 of driving
    + late departure                         weight   1  → tie-breaker: leave later only to avoid waiting
    + fixed cost per vehicle used            big (fewest_vehicles) / tiny (others)
    + global span                            balanced strategy only
* Any order can be dropped at a huge penalty, so a plan is always returned and
  the unplannable orders are reported with a reason.
"""
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

DROP_PENALTY = 10 ** 9


def solve_vrp(nodes, dist_m, time_s, vehicles, opts):
    """
    nodes:    list of dicts {kind: depot|order|reload, mi: matrix index, demand, service_s,
                             tw: (start_s, end_s) | None, feasible: bool}
    dist_m:   MxM int metres (M = number of distinct locations), time_s: MxM int seconds
    vehicles: list of {id, name, capacity_kg}
    opts:     {objective: time|distance, strategy, horizon_s, time_limit_s}
    """
    n, V = len(nodes), len(vehicles)
    if n <= 1 or V == 0:
        return {"status": "empty", "routes": [], "dropped": [i for i in range(1, n) if nodes[i]["kind"] == "order"]}

    horizon = int(opts["horizon_s"])
    objective = opts.get("objective", "time")
    strategy = opts.get("strategy", "fewest_vehicles")
    caps = [int(v["capacity_kg"]) for v in vehicles]
    max_cap = max(caps)
    mi = [nd["mi"] for nd in nodes]

    # Cost weights (see module docstring). Travel costs ARC per second/metre, waiting
    # costs SPAN per second (≈1/3 of driving), leaving later than planned costs START
    # per second (tiny tie-breaker → vehicles leave as early as useful).
    if objective == "time":
        def arc(a, b):
            return 100 * int(time_s[mi[a]][mi[b]])
        SPAN, START, FIXED_BIG, FIXED_SMALL, GLOBAL = 50, 1, 150 * 45 * 60, 150 * 300, 80
    else:
        def arc(a, b):
            return 100 * int(dist_m[mi[a]][mi[b]])
        SPAN, START, FIXED_BIG, FIXED_SMALL, GLOBAL = 400, 1, 100 * 20_000, 100 * 2_500, 640

    manager = pywrapcp.RoutingIndexManager(n, V, 0)
    routing = pywrapcp.RoutingModel(manager)
    solver = routing.solver()

    def cost_cb(i, j):
        return arc(manager.IndexToNode(i), manager.IndexToNode(j))

    def time_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return int(time_s[mi[a]][mi[b]]) + int(nodes[a].get("service_s", 0))

    def dist_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return int(dist_m[mi[a]][mi[b]])

    def demand_cb(i):
        nd = nodes[manager.IndexToNode(i)]
        return -max_cap if nd["kind"] == "reload" else int(nd.get("demand", 0))

    c_cb = routing.RegisterTransitCallback(cost_cb)
    t_cb = routing.RegisterTransitCallback(time_cb)
    d_cb = routing.RegisterTransitCallback(dist_cb)
    q_cb = routing.RegisterUnaryTransitCallback(demand_cb)

    routing.SetArcCostEvaluatorOfAllVehicles(c_cb)

    # Load: slack is only allowed at reload nodes → there the load can reset to 0
    routing.AddDimensionWithVehicleCapacity(q_cb, max_cap, caps, True, "Load")
    routing.AddDimension(t_cb, horizon, horizon, False, "Time")
    routing.AddDimension(d_cb, 0, 10 ** 8, True, "Dist")
    # Work = driving + service only (no waiting, start fixed at 0) → used to balance workload
    routing.AddDimension(t_cb, 0, horizon, True, "Work")
    ldim = routing.GetDimensionOrDie("Load")
    tdim = routing.GetDimensionOrDie("Time")
    wdim = routing.GetDimensionOrDie("Work")

    tdim.SetSpanCostCoefficientForAllVehicles(SPAN)
    if strategy == "fewest_vehicles":
        routing.SetFixedCostOfAllVehicles(FIXED_BIG)
    else:
        routing.SetFixedCostOfAllVehicles(FIXED_SMALL)
    if strategy == "balanced":
        wdim.SetGlobalSpanCostCoefficient(GLOBAL)   # penalise the longest working time

    for node in range(1, n):
        nd = nodes[node]
        idx = manager.NodeToIndex(node)
        if nd["kind"] == "reload":
            routing.AddDisjunction([idx], 0)          # optional, free
            continue
        solver.Add(ldim.SlackVar(idx) == 0)           # no load "reset" at customers
        routing.AddDisjunction([idx], DROP_PENALTY)
        if not nd.get("feasible", True):
            solver.Add(routing.ActiveVar(idx) == 0)   # pre-flagged impossible
            continue
        tw = nd.get("tw") or (0, horizon)
        lo, hi = int(max(0, tw[0])), int(min(horizon, tw[1]))
        if lo > hi:
            solver.Add(routing.ActiveVar(idx) == 0)
            continue
        tdim.CumulVar(idx).SetRange(lo, hi)

    for v in range(V):
        s, e = routing.Start(v), routing.End(v)
        solver.Add(ldim.SlackVar(s) == 0)
        tdim.CumulVar(s).SetRange(0, horizon)
        tdim.SetCumulVarSoftUpperBound(s, 0, START)   # small cost for leaving later than planned
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(s))
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(e))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(int(opts.get("time_limit_s", 8)))

    sol = routing.SolveWithParameters(params)
    if sol is None:
        return {"status": "no_solution", "routes": [], "dropped": [i for i in range(1, n) if nodes[i]["kind"] == "order"]}

    val = sol.Value
    routes, served = [], set()
    for v in range(V):
        idx = routing.Start(v)
        seq = []
        while not routing.IsEnd(idx):
            seq.append((manager.IndexToNode(idx), val(tdim.CumulVar(idx))))
            idx = val(routing.NextVar(idx))
        if not any(nodes[nd]["kind"] == "order" for nd, _ in seq):
            routes.append({"vehicle": v, "used": False, "trips": []})
            continue

        trips = []
        trip = {"stops": [], "depart_s": seq[0][1]}
        prev_node, prev_start = seq[0]
        cum_m = cum_load = 0

        def close_trip(prev_node, prev_start, trip, cum_m, arrive_s=None):
            back_s = int(time_s[mi[prev_node]][0])
            back_m = int(dist_m[mi[prev_node]][0])
            arr = prev_start + int(nodes[prev_node].get("service_s", 0)) + back_s
            trip["return"] = {"arrival_s": arr, "leg_m": back_m, "leg_s": back_s}
            trip["total_m"] = cum_m + back_m
            trip["load"] = sum(nodes[s["node"]]["demand"] for s in trip["stops"])
            trip["drive_s"] = sum(s["leg_s"] for s in trip["stops"]) + back_s
            trip["service_s"] = sum(int(nodes[s["node"]].get("service_s", 0)) for s in trip["stops"])
            trip["wait_s"] = sum(s["wait_s"] for s in trip["stops"])
            if trip["stops"]:
                first = trip["stops"][0]
                trip["latest_depart_s"] = first["start_s"] - first["leg_s"]
            else:
                trip["latest_depart_s"] = trip["depart_s"]
            trips.append(trip)

        for node, start in seq[1:]:
            nd = nodes[node]
            if nd["kind"] == "reload":
                if trip["stops"]:
                    close_trip(prev_node, prev_start, trip, cum_m)
                    reload_arrival = trips[-1]["return"]["arrival_s"]
                    trip = {"stops": [], "depart_s": start + int(nd.get("service_s", 0)),
                            "reload_wait_s": max(0, start - reload_arrival)}
                    cum_m = 0
                prev_node, prev_start = node, start
                continue
            travel = int(time_s[mi[prev_node]][mi[node]])
            leg_m = int(dist_m[mi[prev_node]][mi[node]])
            arrival = prev_start + int(nodes[prev_node].get("service_s", 0)) + travel
            wait = max(0, start - arrival)
            cum_m += leg_m
            cum_load += int(nd.get("demand", 0))
            trip["stops"].append({
                "node": node, "arrival_s": arrival, "start_s": start,
                "depart_s": start + int(nd.get("service_s", 0)),
                "wait_s": wait, "leg_m": leg_m, "leg_s": travel, "cum_m": cum_m,
            })
            served.add(node)
            prev_node, prev_start = node, start
        if trip["stops"]:
            close_trip(prev_node, prev_start, trip, cum_m)

        routes.append({
            "vehicle": v, "used": True, "trips": trips,
            "start_s": trips[0]["latest_depart_s"],
            "end_s": trips[-1]["return"]["arrival_s"],
            "total_m": sum(t["total_m"] for t in trips),
            "drive_s": sum(t["drive_s"] for t in trips),
            "service_s": sum(t["service_s"] for t in trips),
            "wait_s": sum(t["wait_s"] for t in trips) + sum(t.get("reload_wait_s", 0) for t in trips),
            "load": cum_load,
            "max_trip_load": max(t["load"] for t in trips),
        })

    dropped = [i for i in range(1, n) if nodes[i]["kind"] == "order" and i not in served]
    return {"status": "ok", "routes": routes, "dropped": dropped, "objective": sol.ObjectiveValue()}


def baseline_nearest_neighbour(nodes, dist_m, time_s, vehicles):
    """
    What a typical hand-made plan looks like: always drive to the nearest
    remaining customer, return to the depot when the vehicle is full, repeat.
    Ignores time windows. Used only to show the saving of the optimised plan.
    """
    orders = [i for i in range(1, len(nodes)) if nodes[i]["kind"] == "order" and nodes[i].get("feasible", True)]
    unserved = set(orders)
    caps = [int(v["capacity_kg"]) for v in vehicles] or [10 ** 9]
    total_m = total_s = 0
    trips = vi = 0
    while unserved:
        cap = caps[vi % len(caps)]
        vi += 1
        cur, load = 0, 0
        while True:
            cands = [j for j in unserved if load + int(nodes[j].get("demand", 0)) <= cap]
            if not cands:
                break
            j = min(cands, key=lambda j: dist_m[nodes[cur]["mi"]][nodes[j]["mi"]])
            total_m += dist_m[nodes[cur]["mi"]][nodes[j]["mi"]]
            total_s += time_s[nodes[cur]["mi"]][nodes[j]["mi"]] + int(nodes[j].get("service_s", 0))
            load += int(nodes[j].get("demand", 0))
            unserved.remove(j)
            cur = j
        if cur == 0:
            break
        trips += 1
        total_m += dist_m[nodes[cur]["mi"]][0]
        total_s += time_s[nodes[cur]["mi"]][0]
    return {"dist_m": total_m, "time_s": total_s, "trips": trips}
