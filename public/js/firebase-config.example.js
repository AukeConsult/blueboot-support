/**
 * firebase-config.example.js — template only, no real values.
 * Copy this file to firebase-config.js and fill in your project values.
 * firebase-config.js is in .gitignore and is never committed.
 *
 * Values from: Firebase Console → blueboot-market → Project Settings → Your apps
 */
window.FIREBASE_CONFIG = {
  apiKey:            "AIzaSy...",
  authDomain:        "blueboot-market.firebaseapp.com",
  projectId:         "blueboot-market",
  storageBucket:     "blueboot-market.appspot.com",
  messagingSenderId: "000000000000",
  appId:             "1:000000000000:web:000000000000",
};

// URL of the deployed supportApi Cloud Function
window.SUPPORT_API = "https://us-central1-blueboot-market.cloudfunctions.net/supportApi";
