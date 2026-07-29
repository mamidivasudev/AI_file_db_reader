import os
import pandas as pd

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

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
                        if fitz:
                            try:
                                with fitz.open(full_path) as doc:
                                    content = "\n".join([page.get_text() for page in doc])
                            except Exception as pdf_e:
                                content = f"Error reading PDF: {str(pdf_e)}"
                        else:
                            content = "PyMuPDF not installed. Cannot read PDF."
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

                except Exception as e:
                    print(f"Error processing {file}: {str(e)}")

    return files_data