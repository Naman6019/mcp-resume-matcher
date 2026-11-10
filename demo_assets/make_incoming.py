"""Builds the DOCX demo resume dropped into resumes/ during the watch_directory demo."""
from pathlib import Path
from docx import Document

doc = Document()
doc.add_heading("Tomas Novak", 0)
doc.add_paragraph("Junior Frontend Developer")
doc.add_paragraph("tomas.novak@example.com | +1 (555) 010-0149 | Austin, TX")
doc.add_heading("Professional Summary", 1)
doc.add_paragraph("Frontend developer with 2 years of experience building React interfaces. "
                  "Learning TypeScript and some Python for small automation scripts.")
doc.add_heading("Experience", 1)
doc.add_paragraph("Frontend Developer - Brightloop Agency (2023 - Present)")
doc.add_paragraph("Built React and CSS landing pages for 15 clients; wrote Jest tests for shared components.")
doc.add_heading("Skills", 1)
table = doc.add_table(rows=3, cols=2)
for row, (k, v) in zip(table.rows, [("Languages", "JavaScript, HTML, CSS, basic Python"),
                                     ("Frameworks", "React, Tailwind CSS, Jest"),
                                     ("Tools", "Git, Figma, Vite")]):
    row.cells[0].text, row.cells[1].text = k, v
out = Path(__file__).parent / "incoming" / "resume_tomas_novak.docx"
doc.save(out)
print("wrote", out)
