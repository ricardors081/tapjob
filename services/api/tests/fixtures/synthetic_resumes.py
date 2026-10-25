"""Synthetic PDF Resumes Generator for Document-Native Layout Evals A through I.

Generates genuine, valid PDF 1.4 documents containing real 2D spatial positioning:
- A: Single column
- B: Real two-column layout (skills & education left, experience right)
- C: Sidebar on left (skills/languages/location) + main experience area on right
- D: Candidate name and professional headline in close proximity without labels
- E: Managerial/technical skills alongside languages in distinct side-by-side columns
- F: Creative headings ("Minha Trajetória", "Onde Estudei", "O Que Domino", "Línguas que Falo")
- G: Real two-page document with provenance across pages
- H: Non-linear education timeline / card layout
- I: Modular grid blocks without obvious top-to-bottom linear order

Zero PII: All names, organizations, and details are 100% synthetic.
"""

from __future__ import annotations

from collections.abc import Sequence
import io
from pathlib import Path

from pypdf import PdfReader


def build_pdf_bytes(
    pages_blocks: Sequence[Sequence[tuple[int, int, int, str] | tuple[int, int, str]]],
) -> bytes:
    """Builds a compliant PDF 1.4 binary stream with valid xref table and Helvetica fonts."""
    buf = io.BytesIO()

    def write(s: str | bytes) -> None:
        buf.write(s.encode("latin1") if isinstance(s, str) else s)

    write("%PDF-1.4\n")
    num_pages = len(pages_blocks)
    font_obj_id = 3 + num_pages
    content_start_id = font_obj_id + 1

    obj_offsets: dict[int, int] = {}

    def start_obj(n: int) -> None:
        obj_offsets[n] = buf.tell()
        write(f"{n} 0 obj\n")

    def end_obj() -> None:
        write("endobj\n")

    # Catalog
    start_obj(1)
    write("<< /Type /Catalog /Pages 2 0 R >>\n")
    end_obj()

    # Pages
    page_ids = [3 + i for i in range(num_pages)]
    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    start_obj(2)
    write(f"<< /Type /Pages /Kids [{kids_str}] /Count {num_pages} >>\n")
    end_obj()

    # Content streams
    content_data: list[bytes] = []
    for blocks in pages_blocks:
        c_buf = io.BytesIO()
        c_buf.write(b"BT\n")
        for block in blocks:
            if len(block) == 3:
                x, y, text = block
                size = 11
            else:
                x, y, size, text = block
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            c_buf.write(f"/F1 {size} Tf\n1 0 0 1 {x} {y} Tm\n({escaped}) Tj\n".encode("latin1"))
        c_buf.write(b"ET\n")
        content_data.append(c_buf.getvalue())

    # Page objects
    for i in range(num_pages):
        pid = 3 + i
        cid = content_start_id + i
        start_obj(pid)
        write(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_obj_id} 0 R >> >> /Contents {cid} 0 R >>\n"
        )
        end_obj()

    # Font object
    start_obj(font_obj_id)
    write("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n")
    end_obj()

    # Contents objects
    for i, c_bytes in enumerate(content_data):
        cid = content_start_id + i
        start_obj(cid)
        write(f"<< /Length {len(c_bytes)} >>\nstream\n")
        write(c_bytes)
        write("\nendstream\n")
        end_obj()

    # xref table
    xref_offset = buf.tell()
    total_objs = content_start_id + num_pages
    write(f"xref\n0 {total_objs}\n")
    write("0000000000 65535 f \n")
    for o in range(1, total_objs):
        offset = obj_offsets[o]
        write(f"{offset:010d} 00000 n \n")

    write(f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n")
    return buf.getvalue()


# Layout definitions A through I

LAYOUT_A_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        (50, 790, 16, "Carlos Eduardo Mendes"),
        (50, 765, 12, "Engenheiro de Software Senior"),
        (50, 740, 10, "Experiencia Profissional"),
        (50, 720, 11, "Tech Solutions - Lider Tecnico (2021 - 2024)"),
        (50, 700, 10, "Desenvolvimento de microsservicos e arquitetura distribuida."),
        (50, 670, 10, "Formacao Academica"),
        (
            50,
            650,
            11,
            "Universidade de Campinas - Bacharelado em Ciencia da Computacao (2016 - 2020)",
        ),
        (50, 620, 10, "Habilidades"),
        (50, 600, 10, "Python, PostgreSQL, Docker"),
        (50, 570, 10, "Idiomas"),
        (50, 550, 10, "Portugues: Nativo, Ingles: Avancado"),
    ]
]

LAYOUT_B_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        (50, 800, 16, "Beatriz Costa"),
        (50, 775, 12, "Gerente de Produto"),
        # Left column: Skills & Education
        (50, 730, 11, "Habilidades Tecnicas"),
        (50, 710, 10, "Gestao de Stakeholders"),
        (50, 690, 10, "Scrum"),
        (50, 670, 10, "SQL"),
        (50, 640, 11, "Educacao"),
        (50, 620, 10, "Fundacao Getulio Vargas"),
        (50, 605, 10, "Bacharelado em Administracao (2017 - 2021)"),
        (50, 575, 11, "Idiomas"),
        (50, 555, 10, "Portugues: Nativo"),
        (50, 540, 10, "Ingles: Fluente"),
        # Right column: Experience
        (280, 730, 11, "Experiencia Profissional"),
        (280, 710, 10, "Empresa Alpha"),
        (280, 695, 10, "Product Manager (2021 - 2024)"),
        (280, 675, 9, "Lideranca de roadmap estrategico e gestao de produto."),
    ]
]

LAYOUT_C_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        # Right main area: Name, Title, Experience
        (240, 790, 16, "Lucas Ferreira"),
        (240, 765, 12, "Especialista DevOps"),
        (240, 730, 11, "Trajetoria Profissional"),
        (240, 710, 10, "Cloud Corp"),
        (240, 695, 10, "DevOps Engineer (2019 - 2023)"),
        (240, 675, 9, "Implementacao de pipelines CI/CD e infraestrutura como codigo."),
        (240, 640, 11, "Educacao"),
        (240, 620, 10, "FIAP - Engenharia de Software (2015 - 2019)"),
        # Left sidebar: Languages, Tech competencies, Location
        (40, 730, 11, "Idiomas"),
        (40, 710, 10, "Portugues - Nativo"),
        (40, 695, 10, "Espanhol - Intermediario"),
        (40, 660, 11, "Competencias"),
        (40, 640, 10, "Kubernetes"),
        (40, 625, 10, "Terraform"),
        (40, 610, 10, "Cloud AWS"),
    ]
]

LAYOUT_D_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        # Name and headline in immediate vertical proximity without labels
        (50, 800, 18, "Mariana Lima"),
        (50, 780, 12, "Diretora de Operacoes"),
        (50, 740, 11, "Experiencia"),
        (50, 720, 10, "Logistica Brasil"),
        (50, 705, 10, "Diretora de Operacoes (2018 - 2023)"),
        (50, 670, 11, "Formacao"),
        (50, 650, 10, "Universidade de Sao Paulo - Engenharia de Producao"),
        (50, 620, 11, "Competencias"),
        (50, 600, 10, "Planejamento Estrategico, Gestao de Equipes"),
        (50, 570, 11, "Idiomas"),
        (50, 550, 10, "Portugues, Ingles"),
    ]
]

LAYOUT_E_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        (50, 800, 16, "Rodrigo Santos"),
        (50, 775, 12, "Coordenador de TI"),
        # Side-by-side: Competencias (Col 1) and Idiomas (Col 2)
        (50, 730, 11, "Competencias e Habilidades"),
        (50, 710, 10, "Lideranca de Pessoas"),
        (50, 695, 10, "Gestao de Conflitos"),
        (50, 680, 10, "Gestao de Stakeholders"),
        (50, 665, 10, "Python"),
        (300, 730, 11, "Idiomas"),
        (300, 710, 10, "Ingles - Fluente"),
        (300, 695, 10, "Frances - Basico"),
        # Below: Experience & Education
        (50, 620, 11, "Historico Profissional"),
        (50, 600, 10, "Inpasa Brasil - Coordenador de TI (2020 - 2024)"),
        (50, 570, 11, "Educacao"),
        (50, 550, 10, "UFMT - Sistemas de Informacao (2015 - 2019)"),
    ]
]

LAYOUT_F_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        (50, 800, 16, "Thiago Rocha"),
        (50, 775, 12, "Arquiteto de Solucoes"),
        (50, 735, 11, "Minha Trajetoria"),
        (50, 715, 10, "Tech Cloud - Solution Architect (2021 - 2024)"),
        (50, 675, 11, "Onde Estudei"),
        (50, 655, 10, "UFSCar - Engenharia da Computacao (2016 - 2020)"),
        (50, 615, 11, "O Que Domino"),
        (50, 595, 10, "Microsservicos, Arquitetura Cloud"),
        (50, 555, 11, "Linguas que Falo"),
        (50, 535, 10, "Portugues: Nativo, Alemao: Intermediario"),
    ]
]

LAYOUT_G_PAGE1: list[tuple[int, int, int, str]] = [
    (50, 800, 16, "Camila Souza"),
    (50, 775, 12, "Cientista de Dados Senior"),
    (50, 735, 11, "Experiencia Recente (Pagina 1)"),
    (50, 715, 10, "Banco Digital - Cientista de Dados (2022 - 2024)"),
    (50, 695, 9, "Construcao de modelos de risco de credito."),
    (50, 660, 11, "Competencias Principais"),
    (50, 640, 10, "Python, Machine Learning, Modelagem Preditiva"),
]

LAYOUT_G_PAGE2: list[tuple[int, int, int, str]] = [
    (50, 800, 11, "Experiencia Anterior (Pagina 2)"),
    (50, 780, 10, "Consultoria Analytics - Analista de BI (2019 - 2022)"),
    (50, 740, 11, "Formacao Academica"),
    (50, 720, 10, "Universidade de Sao Paulo - Bacharelado em Estatistica (2015 - 2019)"),
    (50, 680, 11, "Idiomas"),
    (50, 660, 10, "Portugues: Materno, Ingles: Avancado"),
]

LAYOUT_G_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    LAYOUT_G_PAGE1,
    LAYOUT_G_PAGE2,
]

LAYOUT_H_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        (50, 800, 16, "Juliana Martins"),
        (50, 775, 12, "Engenheira de Machine Learning"),
        (50, 740, 11, "Linha do Tempo de Formacao"),
        # Horizontal / stacked card layout for education
        (
            50,
            715,
            10,
            "[CARD 1] Universidade de Sao Paulo - Pos-graduacao em IA (2022 - 2023)",
        ),
        (
            50,
            690,
            10,
            "[CARD 2] UFMG - Bacharelado em Ciencia da Computacao (2017 - 2021)",
        ),
        (50, 650, 11, "Experiencia"),
        (50, 630, 10, "Inpasa Brasil - ML Engineer (2021 - 2024)"),
        (50, 590, 11, "Competencias"),
        (50, 570, 10, "PyTorch, Python, Gestao de Stakeholders"),
        (50, 530, 11, "Idiomas"),
        (50, 510, 10, "Portugues, Ingles"),
    ]
]

LAYOUT_I_BLOCKS: list[list[tuple[int, int, int, str]]] = [
    [
        # Top Left Box: Name & Title
        (50, 800, 16, "Rafael Alves"),
        (50, 775, 12, "Designer de Produto & UX"),
        # Top Right Box: Summary
        (310, 800, 10, "Resumo: Especialista em design"),
        (310, 785, 10, "systems e pesquisa com usuarios."),
        # Mid Left Box: Skills
        (50, 710, 11, "Habilidades"),
        (50, 690, 10, "Figma, Python, Gestao de Stakeholders"),
        # Mid Right Box: Languages
        (310, 710, 11, "Idiomas"),
        (310, 690, 10, "Portugues, Ingles"),
        # Bottom Left Box: Experience
        (50, 630, 11, "Experiencia"),
        (50, 610, 10, "Inpasa Brasil - Product Designer (2020 - 2024)"),
        # Bottom Right Box: Education
        (50, 560, 11, "Formacao"),
        (50, 540, 10, "Universidade de Sao Paulo - Design Digital (2016 - 2019)"),
    ]
]

LAYOUT_MAP: dict[str, list[list[tuple[int, int, int, str]]]] = {
    "A": LAYOUT_A_BLOCKS,
    "B": LAYOUT_B_BLOCKS,
    "C": LAYOUT_C_BLOCKS,
    "D": LAYOUT_D_BLOCKS,
    "E": LAYOUT_E_BLOCKS,
    "F": LAYOUT_F_BLOCKS,
    "G": LAYOUT_G_BLOCKS,
    "H": LAYOUT_H_BLOCKS,
    "I": LAYOUT_I_BLOCKS,
}


def get_layout_pdf_bytes(variant: str) -> bytes:
    """Returns the real PDF bytes for variant A..I."""
    blocks = LAYOUT_MAP.get(variant.upper())
    if not blocks:
        raise ValueError(f"Unknown layout variant: {variant}")
    return build_pdf_bytes(blocks)


def write_all_fixtures(output_dir: Path) -> dict[str, Path]:
    """Generates and writes all synthetic layout PDFs to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for variant, blocks in LAYOUT_MAP.items():
        pdf_bytes = build_pdf_bytes(blocks)
        target = output_dir / f"layout_{variant.lower()}.pdf"
        target.write_bytes(pdf_bytes)
        paths[variant] = target
    return paths


def extract_pages_text_from_pdf(content: bytes) -> list[str]:
    """Extracts text per page from a PDF binary stream."""
    reader = PdfReader(io.BytesIO(content))
    return [p.extract_text() or "" for p in reader.pages]
