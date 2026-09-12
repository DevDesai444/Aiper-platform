"""Registration, login and the current account."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import DocumentCollaborator, User

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(user: User) -> schemas.TokenOut:
    return schemas.TokenOut(
        access_token=create_access_token(str(user.id), {"email": user.email}),
        user=schemas.UserOut.model_validate(user),
    )


@router.post("/register", response_model=schemas.TokenOut, status_code=status.HTTP_201_CREATED)
async def register(payload: schemas.RegisterRequest, db: DbSession) -> schemas.TokenOut:
    email = payload.email.lower()
    exists = (await db.execute(select(User.id).where(User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        organisation=payload.organisation.strip(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()

    # Claim any share addressed to this email before the account existed.
    await db.execute(
        update(DocumentCollaborator)
        .where(DocumentCollaborator.email == email, DocumentCollaborator.user_id.is_(None))
        .values(user_id=user.id, invite_status="accepted")
    )
    await db.commit()
    await db.refresh(user)
    return _token(user)


@router.post("/login", response_model=schemas.TokenOut)
async def login(payload: schemas.LoginRequest, db: DbSession) -> schemas.TokenOut:
    user = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return _token(user)


@router.get("/me", response_model=schemas.UserOut)
async def me(user: CurrentUser) -> User:
    return user
