"""
Fault injection package for Secure Blackboard SRE.

Exports:
  - CommunicationFaultInjector: mutators for simulating 10 multi-agent failure modes
"""

from clients.graphstate.fault_injection.injector import CommunicationFaultInjector

__all__ = ["CommunicationFaultInjector"]
