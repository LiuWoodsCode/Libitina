#!/usr/bin/env python3
"""Original DSUI Qt controls, matching gtk_reference.py's specimen layout."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('light', 'dark'), default='dark')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[6] / 'dele2' / 'dsui_qss_gen.py')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    if args.screenshot:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                                  QLabel, QFrame, QPushButton, QLineEdit, QComboBox,
                                  QSpinBox, QPlainTextEdit, QCheckBox, QRadioButton,
                                  QSlider, QProgressBar)
    app = QApplication([])
    app.setStyle('Fusion')
    font = QFont('Arial')
    font.setPixelSize(14)
    app.setFont(font)
    source = args.source.expanduser().resolve()
    if not source.is_file():
        parser.error('Pass --source /path/to/dele2/dsui_qss_gen.py')
    spec = importlib.util.spec_from_file_location('dsui_reference', source)
    dsui = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = dsui
    spec.loader.exec_module(dsui)
    dsui.set_theme_mode(args.mode == 'dark')
    app.setStyleSheet(dsui.generate_qt_stylesheet())
    window = QWidget()
    window.setWindowTitle(f'Deletescape UI · Qt · {args.mode}')
    window.resize(600, 900)
    root = QVBoxLayout(window)
    root.setContentsMargins(24, 24, 24, 24)
    root.setSpacing(16)
    root.addWidget(dsui.SectionTitle('Deletescape UI'))
    caption = QLabel('Widget reference / ' + args.mode)
    caption.setProperty('role', 'muted')
    root.addWidget(caption)
    root.addWidget(dsui.Divider())

    def section(title):
        card = QFrame()
        card.setProperty('panel', True)
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(12)
        label = QLabel(title)
        label.setStyleSheet('background: transparent; font-size: 16px; font-weight: 600;')
        box.addWidget(label)
        root.addWidget(card)
        return box

    def row(box, widgets):
        line = QHBoxLayout()
        line.setSpacing(12)
        for widget in widgets:
            line.addWidget(widget, 1)
        box.addLayout(line)

    buttons = section('Actions')
    normal = dsui.styled_button('Default')
    primary = dsui.styled_button('Primary', primary=True)
    disabled = dsui.styled_button('Disabled')
    disabled.setEnabled(False)
    row(buttons, [normal, primary, disabled])
    hover = dsui.styled_button('Hover')
    hover.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    pressed = dsui.styled_button('Pressed')
    pressed.setDown(True)
    keyboard = dsui.styled_button('Keyboard focus')
    row(buttons, [hover, pressed, keyboard])
    fields = section('Fields')
    entry = dsui.styled_line_edit('Your name')
    combo = dsui.styled_combo_box(['Automatic', 'Light', 'Dark'])
    row(fields, [entry, combo])
    spin = dsui.styled_spin_box(42)
    disabled_entry = dsui.styled_line_edit('Unavailable')
    disabled_entry.setEnabled(False)
    row(fields, [spin, disabled_entry])
    text = dsui.styled_text_area('Write a note…')
    text.setFixedHeight(68)
    text.setFrameShape(QFrame.Shape.StyledPanel)
    fields.addWidget(text)
    choices = section('Choices')
    check = QCheckBox('Checkbox')
    checked = QCheckBox('Checked')
    checked.setChecked(True)
    row(choices, [check, checked])
    radio = QRadioButton('Radio')
    radio_on = QRadioButton('Selected')
    radio_on.setChecked(True)
    row(choices, [radio, radio_on])
    row(choices, [dsui.styled_switch('Switch off'), dsui.styled_switch('Switch on', True)])
    levels = section('Levels')
    slider = dsui.styled_slider(65)
    slider.setFixedHeight(24)
    levels.addWidget(slider)
    bar = dsui.styled_progress_bar(65, show_text=False)
    # Stop the initial animation for a deterministic reference.
    bar._value_anim.stop()
    bar.setValue(65)
    levels.addWidget(bar)
    root.addStretch(1)
    foot = QLabel('Pink accent · flat surfaces · thin borders')
    foot.setProperty('role', 'muted')
    root.addWidget(foot)
    window.show()
    keyboard.setFocus()

    def capture():
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError('Could not save screenshot')
        app.quit()

    if args.screenshot:
        QTimer.singleShot(250, capture)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
