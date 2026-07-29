"""Integrations platform — unified third-party integration abstraction.

docs/specs/features/integrations.md (五章) + README §6.17: ONE set of
registration/binding, credential-safe, ingestion and delivery mechanics
shared by every connector. A connector implements exactly three adaptation
points: signature verification, payload normalization, outbound send.
"""
