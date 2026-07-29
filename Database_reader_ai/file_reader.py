import os
import pandas as pd

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    import docx
except ImportError:
    docx = None

BLOCKED_EXTENSIONS = (
    ".pptx",
    ".jpg", ".jpeg", ".png", ".gif",
    ".zip", ".tar", ".gz",
    ".exe", ".dll", ".bin"
)

IGNORE_FOLDERS = {
    ".git",
    ".idea",
    "target",
    "build",
    "node_modules"
}


def read_project(project_path):

    files_data = []

    for root, dirs, files in os.walk(project_path):

        dirs[:] = [d for d in dirs if d not in IGNORE_FOLDERS]

        for file in files:

            if not file.lower().endswith(BLOCKED_EXTENSIONS):

                full_path = os.path.join(root, file)

                try:

                    content = ""
                    lower_file = file.lower()

                    if lower_file.endswith(".pdf"):
                        if PyPDF2:
                            with open(full_path, "rb") as f:
                                reader = PyPDF2.PdfReader(f)
                                content = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
                        else:
                            content = "PyPDF2 not installed. Cannot read PDF."
                    elif lower_file.endswith((".xlsx", ".xls")):
                        df_dict = pd.read_excel(full_path, sheet_name=None)
                        for sheet_name, df in df_dict.items():
                            content += f"--- Sheet: {sheet_name} ---\n"
                            content += df.to_string() + "\n"
                    elif lower_file.endswith(".docx"):
                        if docx:
                            doc = docx.Document(full_path)
                            content = "\n".join([para.text for para in doc.paragraphs])
                        else:
                            content = "python-docx not installed. Cannot read DOCX."
                    else:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()

                    if content.strip():
                        files_data.append({
                            "path": full_path,
                            "filename": file,
                            "content": content
                        })

                except Exception:
                    pass

    return files_data