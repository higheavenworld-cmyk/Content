"""CarPlug Studio – automatische Auto-News-Videos im CarPlug-Branding.

Start: Doppelklick auf "CarPlug-Studio-Starten.bat" (Windows) oder `python app.py`.
"""
from __future__ import annotations

import queue
import threading
import traceback

import gradio as gr

from studio import learn, pipeline, voice
from studio.config import ASSETS, OUTPUT_DIR, load_settings, save_settings
from studio.script import LENGTHS, Scene, Script

HEADERS = ["Sprechertext", "Einblendung", "Bildsuche (englisch)"]


# --- helpers -----------------------------------------------------------------
def script_to_ui(sc: Script):
    rows = [[s.text, s.overlay, s.query] for s in sc.scenes]
    return sc.headline[0], sc.headline[1], sc.headline[2], sc.subline, rows, \
        sc.caption, " ".join(sc.hashtags)


def ui_to_script(h1, h2, h3, sub, rows, caption, tags) -> Script:
    scenes = [Scene(str(r[0]).strip(), str(r[1] or "").strip(), str(r[2] or "").strip())
              for r in (rows or []) if r and str(r[0]).strip()]
    if len(scenes) < 2:
        raise gr.Error("Das Script braucht mindestens 2 Szenen (Hook + Abschluss).")
    return Script([h1 or "", h2 or "", h3 or ""], sub or "", scenes, caption or "", (tags or "").split())


def run_with_log(fn):
    """Run fn(log) in a thread and stream its log lines to the UI."""
    q: queue.Queue = queue.Queue()
    box = {}

    def worker():
        try:
            box["result"] = fn(q.put)
        except Exception as e:  # show the problem in the UI instead of crashing
            box["error"] = f"{e}"
            traceback.print_exc()
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    lines = []
    while (msg := q.get()) is not None:
        lines.append(msg)
        yield "\n".join(lines[-6:]), None
    yield "\n".join(lines[-6:]), box


# --- actions -----------------------------------------------------------------
def do_script(url, files, length):
    try:
        src = pipeline.load_source(url, [f for f in (files or [])])
        sc, info = pipeline.make_script(src, length)
    except Exception as e:
        raise gr.Error(str(e))
    return (src, f"✅ {info} Prüfe und bearbeite das Script unten, dann **② Video erstellen**.",
            *script_to_ui(sc))


def do_video(src, h1, h2, h3, sub, rows, caption, tags, voice_name, use_article, use_free):
    if src is None:
        raise gr.Error("Bitte zuerst ① Script erstellen.")
    sc = ui_to_script(h1, h2, h3, sub, rows, caption, tags)
    for log, box in run_with_log(lambda log: pipeline.make_video(sc, src, voice_name, use_article, use_free, log)):
        if box is None:
            yield log, None, None, "", ""
        elif "error" in box:
            yield f"❌ Fehler: {box['error']}", None, None, "", ""
        else:
            r = box["result"]
            yield (f"✅ Fertig! {r['seconds']:.0f} Sekunden. Ordner: {r['folder']}", str(r["video"]),
                   str(r["cover"]), r["caption"], str(r["folder"]))


def do_all(url, files, length, voice_name, use_article, use_free):
    src, info, *ui = do_script(url, files, length)
    yield (src, info, *ui, "Starte…", None, None, "", "")
    for out in do_video(src, *ui, voice_name, use_article, use_free):
        yield (src, info, *ui, *out)


def do_clone(name, audio):
    if not audio:
        raise gr.Error("Bitte eine Sprachaufnahme hochladen oder aufnehmen.")
    path = voice.save_clone_sample(name, audio)
    note = "" if voice.clone_available() else \
        "\n\n⚠️ Das Klon-Modul ist noch nicht installiert: **Stimme-klonen-installieren.bat** ausführen."
    return f"✅ Gespeichert als „{voice.CLONE_PREFIX}{path.stem}“.{note}", gr.update(choices=voice.voice_choices())


def save_cfg(model, url, pexels, vol, rate):
    s = load_settings()
    s.update(ollama_model=model.strip(), ollama_url=url.strip(), pexels_key=pexels.strip(),
             music_volume=float(vol), speech_rate=f"{int(rate):+d}%")
    save_settings(s)
    return "✅ Gespeichert."


def save_learning(rows, notes):
    learn.update_from_table(rows or [])
    learn.NOTES.write_text(notes or "", encoding="utf-8")
    return "✅ Gespeichert – neue Scripts berücksichtigen das ab jetzt.", learn.table()


# --- layout ------------------------------------------------------------------
CSS = """
.gradio-container {max-width: 1100px !important}
#title h1 {font-style: italic; text-transform: uppercase; letter-spacing: .5px}
#title h1 span {color: #e01414}
"""

with gr.Blocks(title="CarPlug Studio", theme=gr.themes.Base(primary_hue="red", neutral_hue="zinc"),
               css=CSS) as demo:
    with gr.Row():
        gr.Image(str(ASSETS / "logo_source.jpg"), show_label=False, height=90, width=90, container=False,
                 interactive=False, show_download_button=False, show_fullscreen_button=False, scale=0)
        gr.Markdown("# CarPlug <span>Studio</span>\nLink oder Datei rein → fertiges Video im CarPlug-Look raus.",
                    elem_id="title")
    src_state = gr.State(None)

    with gr.Tab("🎬 Video erstellen"):
        with gr.Row():
            url = gr.Textbox(label="Link zum Artikel / Blog", placeholder="https://…", scale=3)
            length = gr.Radio(list(LENGTHS), value="Mittel (~40 s)", label="Länge", scale=2)
        files = gr.File(label="Oder / zusätzlich: Dateien hochladen (Text, PDF, Word, Bilder, Videos)",
                        file_count="multiple", type="filepath")
        with gr.Row():
            voice_dd = gr.Dropdown(voice.voice_choices(), value=voice.voice_choices()[0], label="Stimme", scale=3)
            use_article = gr.Checkbox(True, label="Bilder aus dem Artikel verwenden (Pressefotos)", scale=2)
            use_free = gr.Checkbox(True, label="Freie Bilder suchen (Wikimedia/Openverse)", scale=2)
        with gr.Row():
            btn_script = gr.Button("① Script erstellen", variant="secondary")
            btn_all = gr.Button("⚡ Alles automatisch", variant="primary")
        info = gr.Markdown()

        with gr.Accordion("Script (bearbeitbar)", open=True):
            with gr.Row():
                h1 = gr.Textbox(label="Titel Zeile 1 (weiß)")
                h2 = gr.Textbox(label="Titel Zeile 2 (ROT)")
                h3 = gr.Textbox(label="Titel Zeile 3 (weiß)")
            sub = gr.Textbox(label="Unterzeile (grau)")
            rows = gr.Dataframe(headers=HEADERS, datatype=["str", "str", "str"], col_count=(3, "fixed"),
                                type="array", interactive=True, wrap=True,
                                label="Szenen – 1. Zeile = Hook, letzte = Abschluss")
            with gr.Row():
                caption = gr.Textbox(label="Post-Text", lines=3, scale=3)
                tags = gr.Textbox(label="Hashtags", lines=3, scale=2)
        btn_video = gr.Button("② Video erstellen", variant="primary")
        status = gr.Textbox(label="Status", lines=4, interactive=False)
        with gr.Row():
            out_video = gr.Video(label="Video", height=560)
            with gr.Column():
                out_cover = gr.Image(label="Cover / Thumbnail", height=380)
                out_caption = gr.Textbox(label="Fertiger Post-Text (kopieren)", lines=6, show_copy_button=True)
                out_folder = gr.Textbox(label="Gespeichert in", interactive=False)

    script_fields = [h1, h2, h3, sub, rows, caption, tags]
    video_out = [status, out_video, out_cover, out_caption, out_folder]
    btn_script.click(do_script, [url, files, length], [src_state, info, *script_fields])
    btn_video.click(do_video, [src_state, *script_fields, voice_dd, use_article, use_free], video_out)
    btn_all.click(do_all, [url, files, length, voice_dd, use_article, use_free],
                  [src_state, info, *script_fields, *video_out])

    with gr.Tab("🎙️ Stimme klonen"):
        gr.Markdown(
            "Nimm **20–30 Sekunden** deiner Stimme auf (ruhiger Raum, normal sprechen, kein Musik-Hintergrund). "
            "Danach erscheint sie oben in der Stimmen-Auswahl.\n\n"
            "Das Klonen läuft **lokal auf deinem PC** (Chatterbox, kostenlos, kommerziell nutzbar). "
            "Einmalig **Stimme-klonen-installieren.bat** ausführen (lädt ca. 3–4 GB). "
            "Ohne Grafikkarte dauert ein Video-Voice-Over einige Minuten.\n\n"
            "⚠️ Klone nur deine eigene Stimme oder die von Personen, die zugestimmt haben.")
        clone_status = gr.Markdown("Status: " + ("✅ Klon-Modul installiert" if voice.clone_available()
                                                 else "❌ Klon-Modul noch nicht installiert"))
        clone_name = gr.Textbox(label="Name der Stimme", value="Meine Stimme")
        clone_audio = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Sprachprobe")
        gr.Button("Stimme speichern", variant="primary").click(do_clone, [clone_name, clone_audio],
                                                                [clone_status, voice_dd])

    with gr.Tab("📈 Lernen"):
        gr.Markdown("Trage nach ein paar Tagen die Zahlen deiner Posts ein. Die besten Hooks und die beste Länge "
                    "fließen automatisch in neue Scripts ein (bei Nutzung der lokalen KI).")
        learn_table = gr.Dataframe(learn.table(), headers=["ID", "Datum", "Hook", "Sekunden", "Views", "Likes",
                                                           "Shares", "Watchtime %"],
                                   type="array", interactive=True, wrap=True)
        notes = gr.Textbox(label="Eigene Regeln für alle Scripts (z. B. „Immer mit einer Frage enden“)", lines=3,
                           value=learn.NOTES.read_text(encoding="utf-8") if learn.NOTES.exists() else "")
        learn_msg = gr.Markdown()
        gr.Button("Speichern", variant="primary").click(save_learning, [learn_table, notes],
                                                        [learn_msg, learn_table])

    with gr.Tab("⚙️ Einstellungen"):
        s = load_settings()
        gr.Markdown("Alles funktioniert **ohne Konten**. Die Felder hier sind optionale Verbesserungen.")
        m = gr.Textbox(s["ollama_model"], label="Lokale KI für Scripts (Ollama-Modell) – siehe Anleitung")
        u = gr.Textbox(s["ollama_url"], label="Ollama-Adresse")
        p = gr.Textbox(s["pexels_key"], label="Pexels-Schlüssel (optional, kostenlos → echte Videoclips)",
                       type="password")
        vol = gr.Slider(0, 0.6, value=float(s["music_volume"]), step=0.02, label="Musik-Lautstärke")
        rate = gr.Slider(-10, 30, value=int(str(s["speech_rate"]).rstrip("%")), step=1,
                         label="Sprechtempo (%)")
        cfg_msg = gr.Markdown()
        gr.Button("Speichern", variant="primary").click(save_cfg, [m, u, p, vol, rate], cfg_msg)


if __name__ == "__main__":
    demo.queue().launch(inbrowser=True, server_name="127.0.0.1", allowed_paths=[str(OUTPUT_DIR)])
