import json
import os
from pathlib import Path

SOURCE_FILE = "/home/joy/Downloads/stringMatched.json"
DEST_DIR = "/home/joy/projects/actiblog/data/threads"

def main():
    if not os.path.exists(SOURCE_FILE):
        print(f"Source file not found: {SOURCE_FILE}")
        return

    try:
        with open(SOURCE_FILE, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        return

    Path(DEST_DIR).mkdir(parents=True, exist_ok=True)

    count = 0
    for thread in data:
        # Extract metadata
        ocr_filename = thread.get('ocrFilename', '')
        channel_name = thread.get('channelName', 'unknown')
        
        # Clean up filename for saving
        # If ocrFilename ends in .json, we use it directly, otherwise append .json
        if not ocr_filename:
            continue
            
        safe_filename = os.path.basename(ocr_filename)
        if not safe_filename.endswith('.json'):
            safe_filename += '.json'
            
        # Extract messages
        messages = []
        for msg in thread.get('matchedMessages', []):
            messages.append({
                "author": msg.get('messageAuthorUsername', 'Unknown'),
                "content": msg.get('originalMessageContent', ''),
                "timestamp": msg.get('messageTimestamp', '')
            })
            
        # Create clean thread object
        clean_thread = {
            "id": ocr_filename,
            "channel": channel_name,
            "messages": messages
        }
        
        # Write to file
        dest_path = os.path.join(DEST_DIR, safe_filename)
        with open(dest_path, 'w') as f:
            json.dump(clean_thread, f, indent=2)
            
        count += 1
        
    print(f"Successfully converted {count} threads to {DEST_DIR}")

if __name__ == "__main__":
    main()
