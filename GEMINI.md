# Gemini Collaboration Guidelines

Du agierst als Co-Entwickler für das textbasierte Web-RPG und kooperierst mit Claude.

## Richtlinien
1. Primäre Wissensbasis ist `ARCHITECTURE.md`. Weiche nicht von den dort definierten Datenmodellen ab.
2. Achte auf bestehende Konventionen, die Claude in `app/` angelegt hat.
3. Bevorzuge idiomatischen, typisierten Python-Code (Type Hints, Pydantic v2).
4. Wenn du APIs erweiterst, aktualisiere die entsprechenden Pydantic-Schemas und dokumentiere Änderungen kurz in `ARCHITECTURE.md`.