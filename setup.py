"""agent_session — uniform CLI-agent session abstraction (Claude Code, Codex, …)."""
from setuptools import find_packages, setup

setup(
    name="agent_session",
    version="0.1.0",
    description="Uniform multi-turn session abstraction over CLI coding agents (claude / codex).",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[],
)
