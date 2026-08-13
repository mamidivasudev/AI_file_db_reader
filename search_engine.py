import re

STOP_WORDS = {
    # English stop words
    "what", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "doing",
    "a", "an", "the", "and", "but", "if", "or", "because", "as",
    "until", "while", "of", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "to", "from", "up", "down", "in", "out", "on",
    "off", "over", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "tell", "me", "give", "show",
    "find", "explain", "meaning", "define", "definition",
    # Hindi/Hinglish stop words
    "kya", "hai", "hain", "hoon", "tha", "the", "thi", "ka", "ke", "ki",
    "ko", "se", "me", "mein", "par", "ne", "bhi", "toh", "ye", "yeh",
    "woh", "jo", "aur", "ya", "parantu", "lekin", "kaise", "kab", "kahan",
    "kyun", "kon", "kaun", "kiska", "kisne", "batao", "bataiye", "महे",
    "क्या", "है", "हैं", "का", "के", "की", "को", "से", "में", "पर", "और",
    "या", "बताएं", "बताओ", "यह", "वह", "कौन", "कैसे", "कहाँ"
}

def search_files(question, files_data):
    results = []
    
    words = re.findall(r'\w+', question)
    keywords = [w.lower() for w in words if w.lower() not in STOP_WORDS and len(w) >= 2]
    
    if not keywords:
        keywords = [w.lower() for w in words if len(w) >= 2]

    if not keywords:
        return files_data

    for file in files_data:
        score = 0
        content_lower = file["content"].lower()
        filename_lower = file["filename"].lower()
        
        snippet_indices = []

        for word in keywords:
            if word in filename_lower:
                score += 5
                
            # Find word boundary or exact matches
            pattern = re.escape(word)
            matches = [m.start() for m in re.finditer(pattern, content_lower)]
            
            # Boost score for exact word matches (with word boundaries)
            exact_matches = [m.start() for m in re.finditer(r'\b' + pattern + r'\b', content_lower)]
            score += len(matches) + (len(exact_matches) * 2)
            
            # Extract surrounding context for up to 5 matches
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