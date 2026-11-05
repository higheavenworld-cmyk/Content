"""CarPlug Studio – automatische Auto-News-Videos im CarPlug-Branding.

Start: Doppelklick auf "CarPlug-Studio-Starten.bat" (Windows) oder `python app.py`.
"""
from __future__ import annotations

import queue
import threading
import traceback

import gradio as gr

from studio import learn, pipeline, voice
from studio.config import ASSETS, CUSTOM_FORMAT, FORMATS, LENGTHS, OUTPUT_DIR, load_settings, save_settings
from studio.pipeline import mmss
from studio.script import Scene, Script, estimate_seconds, min_seconds

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


def get_size(fmt, cw, ch):
    if fmt == CUSTOM_FORMAT:
        try:
            w, h = int(cw), int(ch)
        except (TypeError, ValueError):
            raise gr.Error("Bitte Breite und Höhe als Zahlen eingeben.")
        if not (240 <= w <= 3840 and 240 <= h <= 3840):
            raise gr.Error("Breite und Höhe müssen zwischen 240 und 3840 Pixeln liegen.")
        return w - w % 2, h - h % 2
    return FORMATS[fmt]


def length_info(rows, length):
    texts = [str(r[0]) for r in (rows or []) if r and str(r[0]).strip()]
    if not texts:
        return ""
    target = LENGTHS[length]
    est = estimate_seconds(texts)
    need = min_seconds(target)
    if need:
        if est >= need:
            return f"⏱️ Geschätzte Länge: **{mmss(est)}** · Mindestlänge {mmss(need)} wird garantiert ✅"
        if est >= need - 0.6 * max(len(texts) - 2, 0) - 4:
            return (f"⏱️ Geschätzte Länge: **{mmss(est)}** · Mindestlänge {mmss(need)} wird mit Pausen/langsamerem "
                    f"Sprechen erreicht ✅")
        return (f"⚠️ Geschätzte Länge: **{mmss(est)}** – für die Mindestlänge {mmss(need)} fehlen ca. "
                f"{need - est:.0f} s (≈ {(need - est) * 2.4:.0f} Wörter). Szenen ergänzen oder längere Quelle nutzen.")
    return f"⏱️ Geschätzte Länge: **{mmss(est)}** (Ziel {mmss(target)})"


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
def _script_job(url, files, length, log):
    src = pipeline.load_source(url, files, log)
    log("Script wird geschrieben…")
    sc, info = pipeline.make_script(src, LENGTHS[length])
    return src, info, sc


def do_script(url, files, length):
    keep = [gr.update()] * 7
    for log, box in run_with_log(lambda log: _script_job(url, list(files or []), length, log)):
        if box is None:
            yield (gr.update(), "⏳ " + log.replace("\n", "  \n"), *keep)
        elif "error" in box:
            raise gr.Error(box["error"])
        else:
            src, info, sc = box["result"]
            ui = script_to_ui(sc)
            yield (src, f"✅ {info} Prüfe und bearbeite das Script unten, dann **② Video erstellen**.\n\n"
                   + length_info(ui[4], length), *ui)


def do_video(src, h1, h2, h3, sub, rows, caption, tags, voice_name, use_article, use_free, length, fmt, cw, ch):
    if src is None:
        raise gr.Error("Bitte zuerst ① Script erstellen.")
    sc = ui_to_script(h1, h2, h3, sub, rows, caption, tags)
    size = get_size(fmt, cw, ch)
    target = LENGTHS[length]
    for log, box in run_with_log(lambda log: pipeline.make_video(sc, src, voice_name, use_article, use_free,
                                                                 target, size, log)):
        if box is None:
            yield log, None, None, "", ""
        elif "error" in box:
            yield f"❌ Fehler: {box['error']}", None, None, "", ""
        else:
            r = box["result"]
            check = f" (Minimum {mmss(r['min_seconds'])} ✅)" if r["min_seconds"] else ""
            yield (f"✅ Fertig! Länge {mmss(r['seconds'])}{check} · {size[0]}×{size[1]}. Ordner: {r['folder']}",
                   str(r["video"]),
                   str(r["cover"]), r["caption"], str(r["folder"]))


def do_all(url, files, length, fmt, cw, ch, voice_name, use_article, use_free):
    get_size(fmt, cw, ch)  # validate before the long work starts
    last = None
    for out in do_script(url, files, length):
        last = out
        yield (*out, "Quellen werden gelesen…", None, None, "", "")
    src, info, *ui = last
    for out in do_video(src, *ui, voice_name, use_article, use_free, length, fmt, cw, ch):
        yield (src, info, *ui, *out)


def do_clone(name, audio):
    if not audio:
        raise gr.Error("Bitte eine Sprachaufnahme hochladen oder aufnehmen.")
    path = voice.save_clone_sample(name, audio)
    note = "" if voice.clone_available() else \
        "\n\n⚠️ Das Klon-Modul ist noch nicht installiert: **Stimme-klonen-installieren.bat** ausführen."
    return f"✅ Gespeichert als „{voice.CLONE_PREFIX}{path.stem}“.{note}", gr.update(choices=voice.voice_choices())


def save_cfg(model, url, yt_all, browser, vol, rate):
    s = load_settings()
    s.update(ollama_model=model.strip(), ollama_url=url.strip(), youtube_all=bool(yt_all),
             cookies_browser="" if browser == "keiner" else browser,
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

def build_ui() -> gr.Blocks:
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
                url = gr.Textbox(label="Links – Artikel, Blogs, TikTok, YouTube, Instagram, X, Facebook, Bilder, Videos …",
                                 placeholder="https://…  (mehrere Links: einer pro Zeile)", lines=2, scale=3)
            with gr.Row():
                length = gr.Dropdown(list(LENGTHS), value="1 Minute (TikTok-Bonus ✓)", label="Videolänge",
                                     info="Ab 1 Minute ist das Video garantiert etwas länger (z. B. 1:03) – "
                                          "für das TikTok-Creator-Programm.", scale=2)
                fmt = gr.Dropdown(list(FORMATS) + [CUSTOM_FORMAT], value=list(FORMATS)[0], label="Format", scale=3)
                cw = gr.Number(1080, label="Breite (px)", precision=0, visible=False, scale=1, minimum=240,
                               maximum=3840)
                ch = gr.Number(1920, label="Höhe (px)", precision=0, visible=False, scale=1, minimum=240,
                               maximum=3840)
            files = gr.File(label="Oder / zusätzlich: Dateien hochladen (Text, PDF, Word, Bilder, Videos)",
                            file_count="multiple", type="filepath")
            with gr.Row():
                voice_dd = gr.Dropdown(voice.voice_choices(), value=voice.voice_choices()[0], label="Stimme", scale=3)
                use_article = gr.Checkbox(True, label="Bilder aus Artikeln verwenden (Text/Wasserzeichen werden entfernt)",
                                          scale=2)
                use_free = gr.Checkbox(True, label="Freies Footage suchen (YouTube-CC, Wikimedia, Openverse)", scale=2)
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
                length_md = gr.Markdown()
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
        fmt.change(lambda f: (gr.update(visible=f == CUSTOM_FORMAT),) * 2, fmt, [cw, ch])
        rows.change(lambda r, ln: length_info(r, ln), [rows, length], length_md)
        length.change(lambda r, ln: length_info(r, ln), [rows, length], length_md)
        btn_script.click(do_script, [url, files, length], [src_state, info, *script_fields])
        btn_video.click(do_video, [src_state, *script_fields, voice_dd, use_article, use_free, length, fmt, cw, ch],
                        video_out)
        btn_all.click(do_all, [url, files, length, fmt, cw, ch, voice_dd, use_article, use_free],
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
            yt_all = gr.Checkbox(bool(s.get("youtube_all")), label="Auch YouTube-Videos OHNE freie Lizenz als Footage "
                                 "nutzen (mehr Auswahl – Urheberrecht liegt bei dir!)")
            browser = gr.Dropdown(["keiner", "firefox", "edge", "chrome", "brave", "opera"],
                                  value=s.get("cookies_browser") or "keiner",
                                  label="Login für Instagram & Co.: Browser, in dem du eingeloggt bist",
                                  info="Nur nötig, wenn ein Link einen Login verlangt. Firefox funktioniert am zuverlässigsten.")
            vol = gr.Slider(0, 0.6, value=float(s["music_volume"]), step=0.02, label="Musik-Lautstärke")
            rate = gr.Slider(-10, 30, value=int(str(s["speech_rate"]).rstrip("%")), step=1,
                             label="Sprechtempo (%)")
            cfg_msg = gr.Markdown()
            gr.Button("Speichern", variant="primary").click(save_cfg, [m, u, yt_all, browser, vol, rate], cfg_msg)
    return demo


def main() -> None:
    build_ui().queue().launch(inbrowser=True, server_name="127.0.0.1", allowed_paths=[str(OUTPUT_DIR)])


if __name__ == "__main__":
    main()
