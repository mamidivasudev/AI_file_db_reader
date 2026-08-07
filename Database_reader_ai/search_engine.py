import re

def search_files(question, files_data):
    results = []
    
    # Strip punctuation from question to get clean keywords
    clean_question = re.sub(r'[^\w\s]', '', question.lower())
    keywords = [w for w in clean_question.split() if len(w) > 3]
    if not keywords:
        keywords = [w for w in clean_question.split() if len(w) > 2]

    for file in files_data:
        score = 0
        content_lower = file["content"].lower()
        filename_lower = file["filename"].lower()
        
        snippet_indices = []

        for word in keywords:
            if word in filename_lower:
                score += 5
                
            # Find occurrences using simple find()
            idx = content_lower.find(word)
            matches = []
            while idx != -1:
                matches.append(idx)
                # Find next occurrence
                idx = content_lower.find(word, idx + 1)
            
            score += len(matches)
            
            # Extract surrounding context for each match (up to first 5 matches to save context window)
            for idx in matches[:5]:  
                start = max(0, idx - 300)
                end = min(len(file["content"]), idx + 300)
                snippet_indices.append([start, end])

        if score > 0:
            snippet_indices.sort()
            merged_snippets = []
            for start, end in snippet_indices:
                if not merged_snippets:
                    merged_snippets.append([start, end])
                else:
                    last_start, last_end = merged_snippets[-1]
                    if start <= last_end:
                        merged_snippets[-1][1] = max(last_end, end)
                    else:
                        merged_snippets.append([start, end])
                        
            final_content = ""
            for start, end in merged_snippets:
                final_content += f"...{file['content'][start:end]}...\n\n"
            
            snippet_file = file.copy()
            if final_content:
                snippet_file["content"] = final_content
            
            results.append((score, snippet_file))

    results.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in results[:15]]