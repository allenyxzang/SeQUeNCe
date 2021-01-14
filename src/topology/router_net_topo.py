from json5 import load
from mpi4py import MPI
from networkx import Graph, single_source_dijkstra_path
from numpy import mean

from .topology import Topology as Topo
from ..kernel.timeline import Timeline
from ..kernel.p_timeline import ParallelTimeline, AsyncParallelTimeline
from .node import BSMNode, QuantumRouter
from ..components.optical_channel import QuantumChannel, ClassicalChannel


class RouterNetTopo(Topo):
    ALL_GROUP = "groups"
    ASYNC = "async"
    BSM_NODE = "BSMNode"
    GROUP = "group"
    IP = "ip"
    IS_PARALLEL = "is_parallel"
    LOOKAHEAD = "lookahead"
    MEET_IN_THE_MID = "meet_in_the_middle"
    MEMO_ARRAY_SIZE = "memo_size"
    PORT = "port"
    PROC_NUM = "process_num"
    QUANTUM_ROUTER = "QuantumRouter"
    SYNC = "sync"

    def __init__(self, conf_file_name: str):
        self.bsm_to_router_map = {}
        super().__init__(conf_file_name)

    def _load(self, filename):
        with open(filename, 'r') as fh:
            config = load(fh)

        self._add_timeline(config)
        self._map_bsm_routers(config)
        self._add_nodes(config)
        self._add_bsm_node_to_router()
        self._add_qchannels(config)
        self._add_cchannels(config)
        self._add_cconnections(config)
        # quantum connections is supported by sequential simulation so far
        if not config[self.IS_PARALLEL]:
            self._add_qconnections(config)
        self._generate_forwaring_table(config)

    def _add_timeline(self, config):
        stop_time = config.get(Topo.STOP_TIME, float('inf'))
        if config.get(self.IS_PARALLEL, False):
            assert MPI.COMM_WORLD.Get_size() == config[self.PROC_NUM]
            rank = MPI.COMM_WORLD.Get_rank()

            tl_type = config[self.ALL_GROUP][rank][Topo.TYPE]
            lookahead = config[self.LOOKAHEAD]
            ip = config[self.IP]
            port = config[self.PORT]
            if tl_type == self.SYNC:
                self.tl = ParallelTimeline(lookahead, qm_ip=ip, qm_port=port,
                                           stop_time=stop_time)
            elif tl_type == self.ASYNC:
                self.tl = AsyncParallelTimeline(lookahead, qm_ip=ip,
                                                qm_port=port,
                                                stop_time=stop_time)
            else:
                raise NotImplementedError("Unknown type of timeline")
        else:
            self.tl = Timeline(stop_time)

    def _map_bsm_routers(self, config):
        for qc in config[Topo.ALL_Q_CHANNEL]:
            src, dst = qc[Topo.SRC], qc[Topo.DST]
            if dst in self.bsm_to_router_map:
                self.bsm_to_router_map[dst].append(src)
            else:
                self.bsm_to_router_map[dst] = [src]

    def _add_nodes(self, config):
        rank = MPI.COMM_WORLD.Get_rank()
        size = MPI.COMM_WORLD.Get_size()

        for node in config[Topo.ALL_NODE]:
            seed, type = node[Topo.SEED], node[Topo.TYPE],
            group, name = node[self.GROUP], node[Topo.NAME]
            assert group < size, "Group id is out of scope" \
                                 " ({} >= {}).".format(group, size)
            if group == rank:
                if type == self.BSM_NODE:
                    others = self.bsm_to_router_map[name]
                    node_obj = BSMNode(name, self.tl, others)
                elif type == self.QUANTUM_ROUTER:
                    memo_size = node.get(self.MEMO_ARRAY_SIZE, 0)
                    if memo_size:
                        node_obj = QuantumRouter(name, self.tl, memo_size)
                    else:
                        print("the size of memory on quantum router {} "
                              "is not set".format(name))
                        node_obj = QuantumRouter(name, self.tl)
                else:
                    raise NotImplementedError("Unknown type of node")

                node_obj.set_seed(seed)
                if type in self.nodes:
                    self.nodes[type].append(node_obj)
                else:
                    self.nodes[type] = [node_obj]
            else:
                self.tl.add_foreign_entity(name, group)

    def _add_bsm_node_to_router(self):
        for bsm in self.bsm_to_router_map:
            r0_str, r1_str = self.bsm_to_router_map[bsm]
            r0 = self.tl.get_entity_by_name(r0_str)
            r1 = self.tl.get_entity_by_name(r1_str)
            if r0 is not None:
                r0.add_bsm_node(bsm, r1_str)
            if r1 is not None:
                r1.add_bsm_node(bsm, r0_str)

    def _add_qconnections(self, config):
        for q_connect in config.get(Topo.ALL_QC_CONNECT, []):
            node1 = q_connect[Topo.CONNECT_NODE_1]
            node2 = q_connect[Topo.CONNECT_NODE_2]
            attenuation = q_connect[Topo.ATTENUATION]
            distance = q_connect[Topo.DISTANCE] // 2
            type = q_connect[Topo.TYPE]
            cc_delay = []
            for cc in self.cchannels:
                if cc.sender.name == node1 and cc.receiver == node2:
                    cc_delay.append(cc.delay)
                elif cc.sender.name == node2 and cc.receiver == node1:
                    cc_delay.append(cc.delay)
            cc_delay = mean(cc_delay) // 2
            if type == self.MEET_IN_THE_MID:
                name = "BSM.{}.{}.auto".format(node1, node2)
                bsm_node = BSMNode(name, self.tl, [node1, node2])
                self.bsm_to_router_map[bsm_node] = [node1, node2]
                self.nodes[self.BSM_NODE].append(bsm_node)
                for src, dst in zip([node1, node2], [node2, node1]):
                    qc_name = "QC.{}.{}".format(src, bsm_node.name)
                    qc_obj = QuantumChannel(qc_name, self.tl, attenuation,
                                            distance)
                    src_obj = self.tl.get_entity_by_name(src)
                    src_obj.add_bsm_node(bsm_node.name, dst)
                    qc_obj.set_ends(src_obj, bsm_node.name)
                    self.qchannels.append(qc_obj)

                    cc_name = "CC.{}.{}".format(src, bsm_node.name)
                    cc_obj = ClassicalChannel(cc_name, self.tl, distance,
                                              cc_delay)
                    cc_obj.set_ends(src_obj, bsm_node.name)
                    self.cchannels.append(cc_obj)

                    cc_name = "CC.{}.{}".format(bsm_node.name, src)
                    cc_obj = ClassicalChannel(cc_name, self.tl, distance,
                                              cc_delay)
                    cc_obj.set_ends(bsm_node, src)
                    self.cchannels.append(cc_obj)
            else:
                raise NotImplementedError("Unknown type of quantum connection")

    def _generate_forwaring_table(self, config):
        graph = Graph()
        for node in config[Topo.ALL_NODE]:
            if node[Topo.TYPE] == self.QUANTUM_ROUTER:
                graph.add_node(node[Topo.NAME])

        costs = {}
        for qc in self.qchannels:
            router, bsm = qc.sender.name, qc.receiver
            if not bsm in costs:
                costs[bsm] = [router, qc.distance]
            else:
                costs[bsm] = [router] + costs[bsm]
                costs[bsm][-1] += qc.distance

        graph.add_weighted_edges_from(costs.values())
        for router in self.nodes[self.QUANTUM_ROUTER]:
            paths = single_source_dijkstra_path(graph, router.name)
            for dst in paths:
                if dst == router.name:
                    continue
                next_hop = paths[dst][1]
                # routing protocol locates at the bottom of the protocol stack
                routing_protocol = router.network_manager.protocol_stack[0]
                routing_protocol.add_forwarding_rule(dst, next_hop)
