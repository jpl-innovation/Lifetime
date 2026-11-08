"""Shared native edition palette. Only dark is offered by the product."""
THEMES = {
    'dark': dict(bg='#0B1426', card='#12203A', text='#FFFFFF',
                 muted='#9FB0CC', border='#22345A', accent='#FFC93C', accent_hover='#FFD761', track='#1B2A46'),
    # Compatibility for existing callers; this is not a selectable edition theme.
    'light': dict(bg='#F3F4F1', card='#FCFCFA', text='#252D2D',
                  muted='#626E6C', border='#D8DEDA', accent='#52796D', track='#E1E6E1'),
}


def stylesheet():
    c = THEMES['dark']
    return f"""
        QWidget {{ background: {c['bg']}; color: {c['text']}; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 14px; }}
        QLabel {{ background: transparent; }}
        QLineEdit, QComboBox, QSpinBox, QDateEdit, QTextBrowser, QTableWidget {{ background: {c['card']}; border: 1px solid {c['border']}; padding: 6px; border-radius: 6px; }}
        QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; padding: 8px 12px; border-radius: 8px; }}
        QPushButton:hover, QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTableWidget:focus {{ border-color: {c['accent']}; }}
        QPushButton:disabled {{ color: {c['muted']}; }}
        QHeaderView::section {{ background: {c['card']}; color: {c['muted']}; padding: 8px; border: none; }}
        QTableWidget {{ selection-background-color: {c['track']}; selection-color: {c['text']}; gridline-color: {c['border']}; }}
        QTabBar::tab {{ background: {c['card']}; padding: 8px 16px; border-bottom: 2px solid {c['border']}; }}
        QTabBar::tab:selected {{ border-bottom-color: {c['accent']}; }}
        QCheckBox::indicator:focus {{ border: 2px solid {c['accent']}; }}
    """
