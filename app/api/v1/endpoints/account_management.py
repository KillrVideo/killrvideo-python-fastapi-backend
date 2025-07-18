from typing import Annotated

from fastapi import APIRouter, HTTPException, status, Depends

from app.models.user import (
    UserCreateRequest,
    UserCreateResponse,
    UserLoginRequest,
    UserLoginResponse,
    User,
    UserProfileUpdateRequest,
)
from app.services import user_service
from app.core.security import create_access_token
from app.api.v1.dependencies import get_current_viewer
from uuid import UUID

router = APIRouter(prefix="/users", tags=["Users"])


@router.post(
    "/register",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new account",
)
async def register_user(user_in: UserCreateRequest):
    existing_user = await user_service.get_user_by_email_from_credentials_table(
        email=user_in.email
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    created_user_doc = await user_service.create_user_in_table(user_in=user_in)

    return UserCreateResponse.model_validate(created_user_doc)


@router.post("/login", response_model=UserLoginResponse, summary="Login → JWT")
async def login_for_access_token(form_data: UserLoginRequest):
    authenticated_user = await user_service.authenticate_user_from_table(
        email=form_data.email, password=form_data.password
    )
    if not authenticated_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        subject=authenticated_user.userid,
        roles=[authenticated_user.account_status],
    )
    return UserLoginResponse(token=access_token, user=authenticated_user)


@router.get("/me", response_model=User, summary="Current user profile")
async def read_users_me(current_user: Annotated[User, Depends(get_current_viewer)]):
    return current_user


@router.put("/me", response_model=User, summary="Update profile")
async def update_users_me(
    profile_update_data: UserProfileUpdateRequest,
    current_user: Annotated[User, Depends(get_current_viewer)],
):
    updated_user = await user_service.update_user_in_table(
        user_id=current_user.userId, update_data=profile_update_data
    )

    if updated_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found for update, this should not happen if token is valid.",
        )
    return updated_user


# ---------------------------------------------------------------------------
# Public user lookup (by ID) – enables /api/v1/users/{userId}
# ---------------------------------------------------------------------------


@router.get(
    "/{user_id_path:uuid}",
    response_model=User,
    summary="Public user profile by ID",
)
async def get_user_by_id(user_id_path: UUID):
    """Return basic user info (firstname, lastname, email, etc.) for a given UUID."""

    user_obj = await user_service.get_user_by_id_from_table(user_id=user_id_path)

    if user_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return user_obj
