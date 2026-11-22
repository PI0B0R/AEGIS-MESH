"""Agents API: phones register their id, everyone sees the list, the admin renames or revokes."""
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import agents


class AgentIn(BaseModel):
    id: str = Field(..., description="UUID, który telefon nadał sobie przy pierwszym uruchomieniu.")
    name: str = Field("Telefon", max_length=80, examples=["Samsung SM-A546B"])
    kind: str = Field("telefon", description="Rodzaj: " + ", ".join(agents.KINDS))


class AgentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    revoked: bool | None = Field(None, description="true = agent nie może już wysyłać danych.")


def register(app: FastAPI, conn) -> None:
    @app.get("/api/agents", tags=["agenci"], summary="Agenci: telefony i skaner, z liczbą znalezisk")
    def list_agents():
        db = conn()
        try:
            return {"agents": agents.listing(db)}
        finally:
            db.close()

    @app.post("/api/agents/register", tags=["agenci"], summary="Telefon zgłasza swój stały identyfikator")
    def register_agent(request: Request, body: AgentIn):
        db = conn()
        try:
            user = getattr(request.state, "user", None) or {}
            return agents.register(db, body.id, body.name, body.kind, user.get("username"))
        finally:
            db.close()

    @app.patch("/api/agents/{agent_id}", tags=["agenci"], summary="Zmień nazwę albo odwołaj agenta (admin)")
    def edit_agent(agent_id: str, body: AgentPatch):
        agent_id = agents.check_id(agent_id)
        values = body.model_dump(exclude_none=True)
        if not values:
            raise ValueError("Nic do zmiany. Pola: name, revoked.")
        if "revoked" in values:
            values["revoked"] = int(values["revoked"])
        db = conn()
        try:
            if not db.execute(f"UPDATE agents SET {', '.join(k + '=?' for k in values)} WHERE id=?",
                              (*values.values(), agent_id)).rowcount:
                raise HTTPException(404, "Nie ma takiego agenta.")
            return next(a for a in agents.listing(db) if a["id"] == agent_id)
        finally:
            db.close()
