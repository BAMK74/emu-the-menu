# Swappable OCR provider, starting with local Tesseract

Receipt and label photos are read through a single OCR interface so the engine can change without touching the rest of the app. We start with local Tesseract on the Pi to keep images private and the app usable offline, accepting lower accuracy on a 2 GB device. Every result is shown for confirmation, and images are discarded after the Stock Items are confirmed. If accuracy proves too poor, switch the provider to an online service or move to a Pi with more RAM.
