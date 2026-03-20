from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from firebase import db
from auth import verify_token
from rate_limit import check_post_rate_limit, check_comment_rate_limit
from firebase_admin import firestore
import uuid
from datetime import datetime, timezone

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://whispr-app.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "Whispr backend is running"}

@app.post("/post")
def create_post(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException
    check_post_rate_limit(uid)

    user_ref = db.collection("users").document(uid)
    user = user_ref.get().to_dict()

    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Post content cannot be empty.")
    if len(content) > 2000:
        raise HTTPException(status_code=400, detail="Post content is too long.")

    post_id = f"p_{uuid.uuid4().hex[:12]}"
    post_data = {
        "postId": post_id,
        "content": content,
        "uid": uid,
        "username": user.get("username"),
        "category": data.get("category", None),
        "likes": 0,
        "likedBy": [],
        "reactions": {},
        "userReactions": {},
        "commentCount": 0,
        "reported": False,
        "deleted": False,
        "pinned": False,
        "score": 0,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }

    is_disappearing = data.get("disappearing", False)
    if is_disappearing:
        from datetime import timedelta
        disappears_at = datetime.now(timezone.utc) + timedelta(hours=24)
        post_data["disappearsAt"] = disappears_at
        post_data["disappearing"] = True

    is_poll = data.get("isPoll", False)
    poll_options = data.get("pollOptions", [])
    if is_poll and poll_options:
        post_data["poll"] = {
            "labels": poll_options,
            "options": {str(i): 0 for i in range(len(poll_options))},
            "votes": {}
        }

    db.collection("posts").add(post_data)

    user_ref.update({
        "lastPostAt": firestore.SERVER_TIMESTAMP,
        "postCount": firestore.Increment(1),
        "firstPostDone": True,
    })

    return {"message": "Post created successfully", "postId": post_id}
@app.post("/like")
def toggle_like(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException
    post_id = data.get("postId")
    if not post_id:
        raise HTTPException(status_code=400, detail="postId is required.")

    # Check if user is banned
    user = db.collection("users").document(uid).get().to_dict()
    if user.get("banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    post_ref = db.collection("posts").document(post_id)
    post = post_ref.get()
    if not post.exists:
        raise HTTPException(status_code=404, detail="Post not found.")

    post_data = post.to_dict()
    liked_by = post_data.get("likedBy", [])
    already_liked = uid in liked_by

    if already_liked:
        # Unlike
        post_ref.update({
            "likedBy": firestore.ArrayRemove([uid]),
            "likes": firestore.Increment(-1),
            "score": firestore.Increment(-2),
        })
        return {"message": "Unliked", "liked": False}
    else:
        # Like
        post_ref.update({
            "likedBy": firestore.ArrayUnion([uid]),
            "likes": firestore.Increment(1),
            "score": firestore.Increment(2),
        })
        return {"message": "Liked", "liked": True}


@app.post("/comment")
def add_comment(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException
    check_comment_rate_limit(uid)

    user_ref = db.collection("users").document(uid)
    user = user_ref.get().to_dict()

    post_id = data.get("postId")
    text = data.get("text", "").strip()
    parent_id = data.get("parentId", None)

    if not post_id:
        raise HTTPException(status_code=400, detail="postId is required.")
    if not text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty.")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Comment is too long.")

    comment_id = f"c_{uuid.uuid4().hex[:12]}"
    comment_data = {
        "commentId": comment_id,
        "postId": post_id,
        "parentId": parent_id,
        "uid": uid,
        "username": user.get("username"),
        "text": text,
        "likes": 0,
        "likedBy": [],
        "createdAt": firestore.SERVER_TIMESTAMP,
    }

    # Write comment
    comment_ref = db.collection("comments").add(comment_data)

    # Update post comment count and score
    db.collection("posts").document(post_id).update({
        "commentCount": firestore.Increment(1),
        "score": firestore.Increment(3),
    })

    # Update user lastCommentAt for rate limiting
    user_ref.update({"lastCommentAt": firestore.SERVER_TIMESTAMP})

    # Send notification to post owner
    post = db.collection("posts").document(post_id).get().to_dict()
    if post and post.get("uid") != uid:
        db.collection("notifications").add({
            "toUid": post.get("uid"),
            "fromUsername": user.get("username"),
            "type": "comment",
            "postId": post_id,
            "commentId": comment_ref[1].id,
            "read": False,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    return {"message": "Comment added", "commentId": comment_id}


@app.post("/react")
def add_reaction(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException
    user = db.collection("users").document(uid).get().to_dict()
    if user.get("banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    post_id = data.get("postId")
    emoji = data.get("emoji")

    if not post_id or not emoji:
        raise HTTPException(status_code=400, detail="postId and emoji are required.")

    post_ref = db.collection("posts").document(post_id)
    post = post_ref.get().to_dict()

    current_reaction = post.get("userReactions", {}).get(uid)

    update_data = {}

    # Remove old reaction if exists
    if current_reaction:
        update_data[f"reactions.{current_reaction}"] = firestore.Increment(-1)

    # Add new reaction if different from current
    if current_reaction != emoji:
        update_data[f"reactions.{emoji}"] = firestore.Increment(1)
        update_data[f"userReactions.{uid}"] = emoji
        # Notify post owner
        if post.get("uid") != uid:
            db.collection("notifications").add({
                "toUid": post.get("uid"),
                "fromUsername": user.get("username"),
                "type": "react",
                "emoji": emoji,
                "postId": post_id,
                "read": False,
                "createdAt": firestore.SERVER_TIMESTAMP,
            })
    else:
        # Same emoji clicked — remove reaction
        update_data[f"userReactions.{uid}"] = firestore.DELETE_FIELD

    post_ref.update(update_data)
    return {"message": "Reaction updated"}
@app.post("/report")
def report_content(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException

    user = db.collection("users").document(uid).get().to_dict()
    if user.get("banned"):
        raise HTTPException(status_code=403, detail="Your account has been banned.")

    target_id = data.get("targetId")
    target_uid = data.get("targetUid")
    report_type = data.get("type")
    reason = data.get("reason", "").strip()

    if not target_id or not report_type:
        raise HTTPException(status_code=400, detail="targetId and type are required.")
    if not reason:
        raise HTTPException(status_code=400, detail="Please provide a reason for the report.")
    if len(reason) > 500:
        raise HTTPException(status_code=400, detail="Reason is too long.")

    # Check if this user already reported this target
    existing = db.collection("reports").where(
        "targetId", "==", target_id
    ).where(
        "reporterUid", "==", uid
    ).limit(1).get()

    if existing:
        raise HTTPException(status_code=400, detail="You have already reported this content.")

    # Write the report
    db.collection("reports").add({
        "type": report_type,
        "targetId": target_id,
        "targetUid": target_uid or None,
        "reason": reason,
        "reporterUid": uid,
        "status": "pending",
        "createdAt": firestore.SERVER_TIMESTAMP,
    })

    # Count total pending reports for this target
    all_reports = db.collection("reports").where(
        "targetId", "==", target_id
    ).where(
        "status", "==", "pending"
    ).get()

    report_count = len(all_reports)

    # Instead of auto-banning — flag for admin review at threshold
    FLAG_THRESHOLD = 5
    if report_count >= FLAG_THRESHOLD:
        # Flag the post for admin review — NOT auto-ban
        if report_type == "post":
            db.collection("posts").document(target_id).update({
                "flagged": True,
                "flaggedAt": firestore.SERVER_TIMESTAMP,
                "flagReason": f"Received {report_count} reports",
            })
        elif report_type == "user" and target_uid:
            db.collection("users").document(target_uid).update({
                "flagged": True,
                "flaggedAt": firestore.SERVER_TIMESTAMP,
                "flagReason": f"Received {report_count} reports",
            })

    return {"message": "Report submitted. Thank you for keeping Whispr safe."}


@app.post("/admin/ban")
def ban_user(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException

    # Verify caller is admin
    caller = db.collection("users").document(uid).get().to_dict()
    if caller.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    target_uid = data.get("targetUid")
    reason = data.get("reason", "No reason provided")
    duration_days = data.get("durationDays", None)

    if not target_uid:
        raise HTTPException(status_code=400, detail="targetUid is required.")
    if target_uid == uid:
        raise HTTPException(status_code=400, detail="You cannot ban yourself.")

    target_ref = db.collection("users").document(target_uid)
    target = target_ref.get().to_dict()

    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    # Build ban data
    ban_data = {
        "banned": True,
        "banReason": reason,
        "bannedAt": firestore.SERVER_TIMESTAMP,
    }

    if duration_days:
        from datetime import timedelta
        ban_until = datetime.now(timezone.utc) + timedelta(days=int(duration_days))
        ban_data["banUntil"] = ban_until

    target_ref.update(ban_data)

    # Add device fingerprint to deviceBans
    fp = target.get("deviceFingerprint")
    if fp:
        db.collection("deviceBans").add({
            "fingerprint": fp,
            "bannedUid": target_uid,
            "reason": reason,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    # Send ban notification to user
    db.collection("notifications").add({
        "toUid": target_uid,
        "type": "ban",
        "message": f"Your account has been banned. Reason: {reason}",
        "read": False,
        "createdAt": firestore.SERVER_TIMESTAMP,
    })

    return {"message": f"User {target.get('username')} has been banned successfully."}


@app.post("/admin/unban")
def unban_user(data: dict, uid: str = Depends(verify_token)):
    from fastapi import HTTPException

    caller = db.collection("users").document(uid).get().to_dict()
    if caller.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    target_uid = data.get("targetUid")
    if not target_uid:
        raise HTTPException(status_code=400, detail="targetUid is required.")

    target_ref = db.collection("users").document(target_uid)
    target_ref.update({
        "banned": False,
        "banReason": firestore.DELETE_FIELD,
        "banUntil": firestore.DELETE_FIELD,
        "bannedAt": firestore.DELETE_FIELD,
    })

    return {"message": "User unbanned successfully."}
@app.post("/signup")
def signup(data: dict):
    from fastapi import HTTPException
    from firebase_admin import auth as firebase_auth
    import random

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    fingerprint = data.get("fingerprint", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    # Check device ban first
    if fingerprint:
        ban_snap = db.collection("deviceBans").where(
            "fingerprint", "==", fingerprint
        ).limit(1).get()
        if ban_snap:
            raise HTTPException(status_code=403, detail="This device has been banned from creating new accounts.")

           # Check bypass list — emails in this list can create multiple accounts
    bypass_list = []
    try:
        bypass_snap = db.collection("settings").document("bypassEmails").get()
        if bypass_snap.exists:
            bypass_list = bypass_snap.to_dict().get("emails", [])
    except:
        pass

    is_bypass = email in [e.lower() for e in bypass_list]

    # One account per device — skip for bypass emails
    if not is_bypass:
        existing_snap = db.collection("users").where(
            "deviceFingerprint", "==", fingerprint
        ).limit(1).get()
        if existing_snap:
            raise HTTPException(status_code=403, detail="An account already exists from this device. Only one account is allowed per device.")
    # Create Firebase Auth account
    try:
        user_record = firebase_auth.create_user(
            email=email,
            password=password,
        )
    except firebase_auth.EmailAlreadyExistsError:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Generate random anonymous username
    adjectives = ["Silent", "Hidden", "Mystic", "Shadow", "Calm", "Swift", "Bright", "Bold", "Clever", "Witty"]
    nouns = ["Falcon", "River", "Storm", "Panda", "Comet", "Tiger", "Ocean", "Phoenix", "Wolf", "Eagle"]
    username = f"{random.choice(adjectives)}{random.choice(nouns)}{random.randint(10, 99)}"

    # Write user doc to Firestore
    profile_data = {
        "uid": user_record.uid,
        "email": email,
        "username": username,
        "role": "user",
        "banned": False,
        "postCount": 0,
        "firstPostDone": False,
        "lastPostAt": None,
        "bookmarks": [],
        "deviceFingerprint": fingerprint,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "termsAcceptedAt": firestore.SERVER_TIMESTAMP,
    }
    db.collection("users").document(user_record.uid).set(profile_data)

    return {"message": "Account created successfully", "uid": user_record.uid, "username": username}