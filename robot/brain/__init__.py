"""
Robot Brain Module.
Cognitive brain, persona, memory, real-time search, and speech I/O for the humanoid robot.
"""

try:
    from robot.brain.brain import RobotBrain
    from robot.brain.ear import RobotEar
    from robot.brain.memory import StructuredMemory
    from robot.brain.mouth import RobotMouth
    from robot.brain.ollama_client import OllamaClient
    from robot.brain.persona import RobotPersona
    from robot.brain.search import WebSearcher
except ImportError:
    from brain.brain import RobotBrain
    from brain.ear import RobotEar
    from brain.memory import StructuredMemory
    from brain.mouth import RobotMouth
    from brain.ollama_client import OllamaClient
    from brain.persona import RobotPersona
    from brain.search import WebSearcher

__all__ = [
    "RobotBrain",
    "StructuredMemory",
    "RobotPersona",
    "WebSearcher",
    "OllamaClient",
    "RobotMouth",
    "RobotEar",
]
