import firebase_admin
from firebase_admin import credentials, firestore

# Connect to Firebase using the service account key
cred = credentials.Certificate("serviceAccount.json")
firebase_admin.initialize_app(cred)

# This is your database connection — import this in other files
db = firestore.client()

print("Firebase connected successfully")