"""Evidence-safe engineering intelligence capabilities.

This package deliberately models repository facts separately from the
LangGraph execution graph.  Its parsers are static and bounded; they never
import application modules, run package scripts, or execute workflow steps.
"""
