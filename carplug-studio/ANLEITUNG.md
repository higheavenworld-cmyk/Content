# CarPlug Studio

Automatische Auto-News-Videos im CarPlug-Branding: in jedem Format und von 15 Sekunden bis 10 Minuten.
Link oder Datei rein, fertiges Video mit Voice-Over, Untertiteln, Musik, Motion Graphics und Post-Text raus.
**Alles läuft kostenlos und ohne Konten.**

## Installation (Windows)

1. Den Ordner `carplug-studio` auf deinen PC kopieren, z. B. nach `Dokumente\carplug-studio`.
2. Doppelklick auf **`CarPlug-Studio-Installieren.bat`**.
   Python wird bei Bedarf automatisch installiert, danach alle Pakete. Das dauert 5–10 Minuten.
3. Danach liegt **„CarPlug Studio“** als Symbol auf dem Desktop. Ein Doppelklick öffnet das Programm im Browser.

> Falls Windows „Der Computer wurde durch Windows geschützt“ meldet: auf **Weitere Informationen → Trotzdem ausführen** klicken.

## Bedienung

1. **Link** zu einem Artikel einfügen und/oder **Dateien hochladen** (Text, PDF, Word, eigene Bilder oder Videos).
2. **Videolänge** und **Format** wählen (siehe unten).
3. **① Script erstellen**: Titel, Hook, Szenen und Einblendungen erscheinen und lassen sich frei bearbeiten.
4. Stimme wählen, dann **② Video erstellen**.
   Alternativ macht **⚡ Alles automatisch** beide Schritte in einem Durchgang.
5. Ergebnis im Ordner `ausgabe\<datum>_<titel>\`:
   - `video.mp4`: fertiges Video
   - `cover.png`: Thumbnail im CarPlug-Look
   - `caption.txt`: Post-Text, Hashtags und Bildnachweise

## Länge (15 Sekunden bis 10 Minuten)

- **Ab 1 Minute ist das Video garantiert etwas länger** als gewählt, z. B. mindestens 1:03 bei „1 Minute“.
  So erfüllt es sicher die Mindestlänge für das TikTok-Creator-Programm.
- Unter dem Script steht die **geschätzte Länge**. Sie aktualisiert sich, sobald du Text änderst.
- Ist der Text knapp zu kurz, verlängert das Tool automatisch die Pausen und spricht etwas langsamer.
  Reicht auch das nicht, bricht es mit einem Hinweis ab, statt ein zu kurzes Video zu liefern.
- Nach dem Rendern wird die echte Dateilänge nochmal geprüft.
- Lange Videos brauchen lange Quellen: Für 10 Minuten werden ca. 1.400 gesprochene Wörter benötigt.
  Mit der lokalen KI (Ollama) gelingt das deutlich besser.

## Format

9:16 (TikTok, Reels, Shorts), 4:5 (Instagram-Feed), 1:1, 3:4, 16:9 (YouTube) und 16:9 in 4K.
Mit **„Eigenes Format“** gibst du Breite und Höhe frei ein (240–3840 Pixel).
Bei Hochformat bleiben alle Texte außerhalb der App-Bedienelemente (Buttons rechts, Text unten, Leiste oben).

## Was automatisch passiert

| Schritt | Kostenlos und ohne Konto |
|---|---|
| Text aus Link oder Datei holen | trafilatura, PDF- und Word-Leser |
| Script mit Hook, Fakten und Call-to-Action | eingebauter Regel-Writer, besser mit lokaler KI (siehe unten) |
| Voice-Over | Microsoft-Neural-Stimmen (6 deutsche Stimmen) |
| Footage | deine Uploads → Bilder aus dem Artikel → Wikimedia Commons und Openverse (freie Lizenzen) |
| Untertitel | wortgenau, aktives Wort in CarPlug-Rot |
| Musik | eigene Tracks aus `assets\music`, sonst ein automatisch erzeugter Beat |
| Motion Graphics | Intro im Thumbnail-Look, Ken-Burns-Zoom, Keyword-Einblendungen, Flash-Übergänge mit Whoosh, Fortschrittsbalken, Outro |

## Optionale Verbesserungen

- **Bessere Scripts (lokale KI, kostenlos):** [Ollama](https://ollama.com/download) installieren und einmal in der
  Eingabeaufforderung `ollama pull qwen2.5:7b` ausführen. CarPlug Studio nutzt die KI dann automatisch.
  Empfohlen sind 16 GB RAM.
- **Eigene Stimme klonen:** **`Stimme-klonen-installieren.bat`** ausführen (lädt ca. 3–4 GB), dann im Tab
  „Stimme klonen“ 20–30 Sekunden aufnehmen. Das läuft lokal mit Chatterbox (MIT-Lizenz, kommerziell nutzbar).
  Mit Nvidia-Grafikkarte geht es schnell, ohne dauert es einige Minuten pro Video.
- **Echte Videoclips statt Fotos:** Bei [pexels.com/api](https://www.pexels.com/api/) einen kostenlosen
  Schlüssel holen und unter „Einstellungen“ eintragen.
- **Eigene Musik:** lizenzfreie MP3s, z. B. aus der YouTube Audio Library, in `assets\music` legen.

## Lernen

Im Tab **„Lernen“** trägst du nach ein paar Tagen Views, Likes, Shares und Watchtime ein.
Die lokale KI bekommt danach die bestbewerteten Hooks und die erfolgreichste Länge als Vorbild.
Eigene Regeln, z. B. „Immer mit einer Frage enden“, gelten für jedes neue Script.

## Rechtliches (wichtig)

- **Artikel-Bilder** sind meist Pressefotos der Hersteller und für redaktionelle Berichterstattung freigegeben.
  Bilder von Fotografen oder Agenturen (dpa, Getty …) dagegen **nicht**. Im Zweifel den Haken
  „Bilder aus dem Artikel“ entfernen und freie Bilder oder eigene Uploads nutzen.
- Freie Bilder brauchen eine **Namensnennung**. Sie steht automatisch in `caption.txt`, also mitposten.
- Stimmen nur klonen, wenn es deine eigene ist oder die Person zugestimmt hat.

## Geplant

- Automatisches Posten auf TikTok, Instagram, YouTube und Facebook (braucht einmalig die Verknüpfung deiner Kanäle)
- Automatisches Einlesen der Statistiken für den Lern-Tab
