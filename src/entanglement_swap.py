"""
Network Topology:

(ALICE)===(CHARLIE)===(BOB)

ALICE:
    Detector

CHARLIE:
    2 SPDC Sources
    BSM
    2 Quantum Memories

BOB:
    Detector
"""
import math
import re

from sequence import encoding
from sequence.process import Process
from sequence.entity import Entity
from sequence.event import Event
from sequence.timeline import Timeline
from sequence import topology


# Protocol
class Swap(Entity):
    def __init__(self, name, timeline, **kwargs):
        super().__init__(name, timeline)
        self.role = kwargs.get("role", -1)  # Alice, Bob, Charlie are 0, 1, 2, respectively

        self.classical_delay = 0
        self.quantum_delay = 0
        self.start_time = 0
        self.light_time = 0
        self.qubit_frequency = 0
        self.raw_bit_list = []
        self.bit_list = []
        self.bit_lengths = [None, None]
        self.indices = [None, None]
        self.parent = None
        self.another_alice = None
        self.another_bob = None
        self.another_charlie = None
        self.sample_size = 0

    def init(self):
        pass

    def assign_node(self, node):
        self.node = node
        cchannel = node.components.get("cchannel")
        qchannel = node.components.get("qchannel")
        if cchannel is not None:
            self.classical_delay = cchannel.delay
        if qchannel is not None:
            self.quantum_delay = int(round(qchannel.distance / qchannel.light_speed))

    def begin_photon_pulse(self):
        # emit photons at both sources
        state = [complex(math.sqrt(1/2)), complex(math.sqrt(1/2))]
        num_photons = self.light_time * self.qubit_frequency
        self.node.send_photons(state, num_photons, "spdc_a")
        self.node.send_photons(state, num_photons, "spdc_b")

    def end_photon_pulse(self):
        # get indices of detection events
        bits = self.node.get_bits(self.light_time, self.start_time, self.qubit_frequency, "detector")
        self.raw_bit_list = bits
        indices = [i for i, b in enumerate(bits) if b != -1]

        # send indices to Charlie
        message = "received_photons {} {}".format(self.role, indices)
        self.node.send_message(message)

    def send_to_bsm(self):
        if [] not in self.indices:
            # get indices
            index_alice = self.indices[0].pop(0)
            index_bob = self.indices[1].pop(0)

            # send corresponding photons to bsm
            memory_alice = self.node.components["memory_a"]
            memory_bob = self.node.components["memory_b"]
            photons_alice = memory_alice.retrieve_photon(index_alice)
            photons_bob = memory_bob.retrieve_photon(index_bob)
            for photon in photons_alice:
                self.node.components["bsm"].get(photon)
            for photon in photons_bob:
                self.node.components["bsm"].get(photon)

            # schedule next send_to_bsm after 1/qubit_frequency
            time = self.timeline.now() + int(1e12 / self.qubit_frequency)
            process = Process(self, "send_to_bsm", [])
            event = Event(time, process)
            self.timeline.schedule(event)

        else:
            bsm_res = self.node.components["bsm"].get_bsm_res()
            message = "bsm_result {}".format(bsm_res)
            self.node.send_message(message, "cc_ac")  # send to Alice
            self.node.send_message(message, "cc_bc")  # send to Bob

    def received_message(self):
        message = self.node.message.split(" ")

        if message[0] == "begin_entanglement_swap":
            # set params
            self.qubit_frequency = float(message[1])
            self.light_time = float(message[2])
            self.start_time = int(message[3])

            # schedule end_photon_pulse()
            process = Process(self, "end_photon_pulse", [])
            event = Event(self.start_time + int(round(self.light_time * 1e12)), process)
            self.timeline.schedule(event)

            # clear detector photon times to restart measurement
            process = Process(self.node.components["detector"], "clear_detectors", [])
            event = Event(int(self.start_time), process)
            self.timeline.schedule(event)

        if message[0] == "received_photons":
            # parse indices
            indices = []
            if message[1] != "[]":  # no matching indices
                for val in message[1:]:
                    indices.append(int(re.sub("[],[]", "", val)))

            # determine if from Alice or Bob
            # store indices
            sender = int(message[1])  # 0 for Alice and 1 for Bob
            self.indices[sender] = indices

            # see if we have both index lists and if so send to BSM
            if None not in self.indices:
                self.send_to_bsm()

        if message[0] == "bsm_result":
            # parse bsm results
            times_and_bits = []  # list of alternating time/bit
            if message[1] != "[]":
                for val in message[1:]:
                    times_and_bits.append(int(re.sub("[],[]", "", val)))
            bsm_res = []
            bsm_single = []
            for i, val in enumerate(times_and_bits):
                bsm_single.append(val)
                if i % 2:
                    bsm_res.append(bsm_single)
                    bsm_single = []

            # get and correct results
            for _ in bsm_res:
                bit = self.raw_bit_list.pop(0)
                if self.role == 1:
                    bit = 1 - bit
                self.bit_list.append(bit)

            # send finished message
            message = "got_bits {} {}".format(self.role, len(self.bit_list))
            self.node.send_message(message)

        if message[0] == "got_bits":
            sender = int(message[1])
            self.bit_lengths[sender] = int(message(2))

            # check if we have both
            if self.bit_lengths[0] == self.bit_lengths[1] and self.bit_lengths[0] is not None:
                if self.bit_lengths[0] < self.sample_size:
                    self.start_protocol()
                else:
                    # finished protocol
                    print("finished entanglement swap")

                    alice_bits = int("".join(str(x) for x in self.another_alice.bit_list), 2)  # convert to int
                    bob_bits = int("".join(str(x) for x in self.another_bob.bit_list), 2)  # convert to int

                    print("Alice measured bits: \t{:0{}b}".format(alice_bits))
                    print("Bob measured bits: \t{:0{}b}".format(bob_bits))

                    bit_diff = alice_bits ^ bob_bits
                    num_errors = 0
                    while bit_diff:
                        bit_diff &= bit_diff - 1
                        num_errors += 1

                    print("error percentage: {}".format(num_errors / self.sample_size))

    def start_protocol(self):
        # reset params
        self.indices = [None, None]

        # set start time
        self.start_time = self.timeline.now() + int(max(round(self.another_alice.classical_delay),
                                                        round(self.another_bob.classical_delay)))

        # notify Alice and Bob that we are starting entanglement swap
        message = "begin_entanglement_swap {} {} {}".format(self.qubit_frequency, self.light_time, self.start_time)
        self.node.send_message(message, "cc_ac")  # send to Alice
        self.node.send_message(message, "cc_bc")  # send to Bob

        # schedule start for begin_photon_pulse

    def generate_pairs(self, sample_size):
        # assert that start_protocol is called from Charlie (middle node)
        assert self.role == 2

        self.sample_size = sample_size
        self.bit_lengths = [0, 0]

        # set qubit frequency
        lightsource_a = self.node.components["spdc_a"]
        lightsource_b = self.node.components["spdc_b"]
        assert lightsource_a.frequency == lightsource_b.frequency
        self.qubit_frequency = lightsource_a.frequency

        # set light_time
        mean_photon_num = min(lightsource_a.mean_photon_num, lightsource_b.mean_photon_num)
        self.light_time = sample_size / (self.qubit_frequency * mean_photon_num)

        self.start_protocol()


if __name__ == "__main__":
    tl = Timeline()

    # Channels
    qc_alice_charlie = topology.QuantumChannel("qc_ac", tl)
    qc_bob_charlie = topology.QuantumChannel("qc_bc", tl)
    cc_alice_charlie = topology.ClassicalChannel("cc_ac", tl)
    cc_bob_charlie = topology.ClassicalChannel("cc_bc", tl)

    # Alice
    spdc_alice = topology.SPDCSource("alice.ls", tl)
    bsm_alice = topology.BSM("alice.bsm", tl)
    detector_alice = topology.QSDetector("alice.qsd", tl)
    alice = topology.Node("alice", tl)

    # Bob
    spdc_bob = topology.SPDCSource("bob.ls", tl)
    bsm_bob = topology.BSM("bob.bsm", tl)
    detector_bob = topology.QSDetector("bob.qsd", tl)
    bob = topology.Node("bob", tl)

    # Charlie
    spdc_charlie_1 = topology.SPDCSource("charlie.ls_1", tl)
    spdc_charlie_2 = topology.SPDCSource("charlie.ls_2", tl)
    bsm_charlie = topology.BSM("charlie.bsm", tl)
    mem_charlie_1 = topology.Memory("charlie.mem_1", tl)
    mem_charlie_2 = topology.Memory("charlie.mem_2", tl)
    charlie = topology.Node("charlie", tl)
