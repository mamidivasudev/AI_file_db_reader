import sys
sys.path.append(r"d:\AI\AI_file_db_reader\Database_reader_ai")
from file_reader import read_project
static_dir = r"\\SAT-HYD-W0007\Vasu\New folder\Database_reader_ai\files"
files_data = read_project(static_dir)

import re

questions = [
    "how to login",
    "how to login to gujrams?"
]

for q in questions:
    clean_question = re.sub(r'[^\w\s]', '', q.lower())
    keywords = [w for w in clean_question.split() if len(w) > 3]
    print(f"\nQuestion: {q}")
    print(f"Keywords: {keywords}")
    for file in files_data:
        print(f"  File: {file['filename']}")
        content_lower = file["content"].lower()
        filename_lower = file["filename"].lower()
        for word in keywords:
            idx = content_lower.find(word)
            count = 0
            while idx != -1:
                count += 1
                idx = content_lower.find(word, idx + 1)
            print(f"    - '{word}': {count} occurrences")
