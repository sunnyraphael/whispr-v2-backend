from datetime import datetime, timezone
from fastapi import HTTPException
from firebase import db

POST_COOLDOWN_SECONDS = 120  # 2 minutes between posts
COMMENT_COOLDOWN_SECONDS = 30  # 30 seconds between comments

def check_post_rate_limit(uid: str):
    """Check if user is posting too fast"""
    user_ref = db.collection("users").document(uid)
    user = user_ref.get()
    
    if not user.exists:
        raise HTTPException(status_code=404, detail="User not found.")
    
    data = user.to_dict()
    
    # Check if banned
    if data.get("banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")
    
    # Check rate limit
    last_post = data.get("lastPostAt")
    if last_post:
        last_post_time = last_post.timestamp()
        now = datetime.now(timezone.utc).timestamp()
        seconds_since = now - last_post_time
        if seconds_since < POST_COOLDOWN_SECONDS:
            wait = int(POST_COOLDOWN_SECONDS - seconds_since)
            raise HTTPException(
                status_code=429,
                detail=f"You are posting too fast. Please wait {wait} seconds."
            )

def check_comment_rate_limit(uid: str):
    """Check if user is commenting too fast"""
    user_ref = db.collection("users").document(uid)
    user = user_ref.get()
    
    if not user.exists:
        raise HTTPException(status_code=404, detail="User not found.")
    
    data = user.to_dict()
    
    if data.get("banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")
    
    last_comment = data.get("lastCommentAt")
    if last_comment:
        last_comment_time = last_comment.timestamp()
        now = datetime.now(timezone.utc).timestamp()
        seconds_since = now - last_comment_time
        if seconds_since < COMMENT_COOLDOWN_SECONDS:
            wait = int(COMMENT_COOLDOWN_SECONDS - seconds_since)
            raise HTTPException(
                status_code=429,
                detail=f"You are commenting too fast. Please wait {wait} seconds."
            )