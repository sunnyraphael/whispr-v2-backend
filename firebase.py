import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# Load credentials from environment variable (production) or file (local dev)
cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

if cred_json:
    cred_dict = json.loads(cred_json)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("serviceAccount.json")

firebase_admin.initialize_app(cred)
db = firestore.client()
