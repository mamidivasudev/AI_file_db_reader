def search_files(question, files_data):

    results = []

    keywords = question.lower().split()

    for file in files_data:

        score = 0

        content = file["content"].lower()
        filename = file["filename"].lower()

        for word in keywords:

            if len(word) < 3:
                continue

            if word in filename:
                score += 5

            score += content.count(word)

        if score > 0:
            results.append((score, file))

    results.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return [item[1] for item in results[:15]]