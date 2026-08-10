import sys
sys.path.append(r"d:\AI\AI_file_db_reader\Database_reader_ai")

from search_engine import search_files
from file_reader import read_project

static_dir = r"\\SAT-HYD-W0007\Vasu\New folder\Database_reader_ai\files"
files_data = read_project(static_dir)

questions = [
    "how to login",
    "how to login to gujrams?",
    "how to login to GUjRAMS?",
    "gujrams login process",
    "Gujrams login process"
]

import re

for q in questions:
    clean_question = re.sub(r'[^\w\s]', '', q.lower())
    keywords = [w for w in clean_question.split() if len(w) > 3]
    if not keywords:
        keywords = [w for w in clean_question.split() if len(w) > 2]
    
    print(f"\nQuestion: {q}")
    print(f"Keywords: {keywords}")
    
    results = search_files(q, files_data)
    print(f"Matched files ({len(results)}):")
    for item in results:
        print(f" - {item['filename']}")

