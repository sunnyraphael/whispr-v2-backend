from firebase_admin import auth
from fastapi import HTTPException, Header

def verify_token(authorization: str = Header(...)):
    """
    Every request from React will include a token in the header.
    This function checks that token is valid and returns the user's uid.
    """
    try:
        # Token comes in as "Bearer xxxxx" — we split to get just the token
        token = authorization.split("Bearer ")[1]
        decoded = auth.verify_id_token(token)
        return decoded["uid"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please log in again.")