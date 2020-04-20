from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...topology.node import Node

from ..message import Message
from .rule_manager import *


class ResourceManagerMessage(Message):
    """
    Two type of ResourceManagerMessage:
    - REQUEST: request eligible protocols from remote resource manager to pair entanglement protocols
      - ini_protocol: protocol that creates this message
      - request_fun: a function using ResourceManager as input to search eligible protocols on remote node
    - RESPONSE: approve or reject received request
      - is_approved: bool type
      - ini_protocol: protocol that creates REQUEST message
      - paired_protocol: protocol that is paired with ini_protocol
    """

    def __init__(self, msg_type, receiver, **kwargs):
        Message.__init__(self, msg_type, receiver)
        self.ini_protocol = kwargs["protocol"]
        if msg_type == "REQUEST":
            self.req_condition_fun = kwargs["req_condition_fun"]
        elif msg_type == "RESPONSE":
            self.is_approved = kwargs["is_approved"]
            self.paired_protocol = kwargs["paired_protocol"]
        else:
            raise Exception("ResourceManagerMessage gets unknown type of message: %s" % str(msg_type))


class ResourceManager():
    def __init__(self, owner: "Node"):
        self.name = "resource_manager"
        self.owner = owner
        self.memory_manager = MemoryManager(owner.memory_array)
        self.memory_manager.set_resource_manager(self)
        self.rule_manager = RuleManager()
        self.rule_manager.set_resource_manager(self)
        # protocols that are requesting remote resource
        self.pending_protocols = []
        # protocols that are waiting request from remote resource
        self.waiting_protocols = []
        self.memory_to_protocol_map = {}

    def load(self, rule: "Rule") -> bool:
        self.rule_manager.load(rule)

        for memory_info in self.memory_manager:
            memories = rule.is_valid(memory_info)
            if len(memories) > 0:
                memory_info.to_occupied()
                rule.do(memories)

        return True

    def update(self, protocol: "Protocol", memory: "Memory", state) -> bool:
        self.memory_manager.update(memory, state)
        self.owner.protocols.remove(protocol)

        # check if any rules have been met
        memo_info = self.memory_manager.get_info_by_memory(memory)
        for rule in self.rule_manager:
            memories = rule.is_valid(memo_info)
            if len(memories) > 0:
                rule.do(memories)
                memo_info.to_occupied()
                break
        return True

    def get_memory_manager(self):
        return self.memory_manager

    def send_request(self, protocol: "Protocol", req_dst: str, req_condition_func: Callable[[List["Protocol"]], None]):
        if req_dst is None:
            self.waiting_protocols.append(protocol)
            return
        self.pending_protocols.append(protocol)
        msg = ResourceManagerMessage("REQUEST", "resource_manager", protocol=protocol,
                                     req_condition_fun=req_condition_func)
        self.owner.send_message(req_dst, msg)

    def received_message(self, src: str, msg: "ResourceManagerMessage") -> None:
        if msg.msg_type == "REQUEST":
            for protocol in self.waiting_protocols:
                if msg.req_condition_fun(protocol):
                    protocol.set_others(msg.ini_protocol)
                    new_msg = ResourceManagerMessage("RESPONSE", "resource_manager", protocol=msg.ini_protocol,
                                                     is_approved=True, paired_protocol=protocol)
                    self.owner.send_message(src, new_msg)
                    self.waiting_protocols.remove(protocol)
                    self.owner.protocols.append(protocol)
                    protocol.start()
                    return

            new_msg = ResourceManagerMessage("RESPONSE", "resource_manager", protocol=msg.ini_protocol,
                                             is_approved=False, paired_protocol=None)
            self.owner.send_message(src, new_msg)
        elif msg.msg_type == "RESPONSE":
            protocol = msg.ini_protocol

            if msg.is_approved:
                protocol.set_others(msg.paired_protocol)
                self.pending_protocols.remove(protocol)
                self.owner.protocols.append(protocol)
                protocol.start()
            else:
                protocol.release()
                self.pending_protocols.remove(protocol)
