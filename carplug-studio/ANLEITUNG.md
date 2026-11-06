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

1. **Links** einfügen (einer pro Zeile) und/oder **Dateien hochladen** (Text, PDF, Word, eigene Bilder oder Videos).
   Das geht mit fast jedem Link: Blogs, News-Seiten, TikTok, YouTube, Instagram, X/Twitter, Facebook, Reddit,
   Pinterest, Vimeo sowie direkte Bild- und Video-Links.
2. **Videolänge** und **Format** wählen (siehe unten).
3. **① Script erstellen**: Titel, Hook, Szenen und Einblendungen erscheinen und lassen sich frei bearbeiten.
4. Stimme wählen, dann **② Footage prüfen** (Bilder ansehen und bei Bedarf tauschen) und **③ Video erstellen**.
   Alternativ macht **⚡ Alles automatisch** alle Schritte in einem Durchgang.
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
| Text aus Links und Dateien | Artikeltext. Bei Videos: Untertitel, Beschreibung oder lokale Spracherkennung |
| Immer auf Deutsch | Fremdsprachige Quellen (z. B. englische YouTube-Videos) werden automatisch übersetzt: lokale KI, sonst Google oder MyMemory (kostenlos, ohne Konto) |
| Script mit Hook, Fakten und Call-to-Action | eingebauter Regel-Writer, besser mit lokaler KI (siehe unten) |
| Voice-Over | Microsoft-Neural-Stimmen. Am natürlichsten sind die ★-Stimmen Florian und Seraphina. Dazu Aussprache-Aufbereitung (km/h, Einheiten, Modellnamen) und Studio-Klang. Am realistischsten ist deine geklonte Stimme |
| Footage | siehe unten |
| Untertitel | wortgenau, aktives Wort in CarPlug-Rot |
| Musik | eigene Tracks aus `assets\music`, sonst ein automatisch erzeugter Beat |
| Motion Graphics | Intro im Thumbnail-Look, Info-Karten mit hochzählenden Zahlen, Story-Fortschrittsbalken, rote Übergänge, Outro |

## Footage: passend, abwechslungsreich, ohne fremde Texte

- **KI-Bildverständnis.** Eine lokale KI (SigLIP 2 von Google, versteht Deutsch) schaut sich jedes Bild und jede
  Videostelle an und vergleicht sie mit dem Thema (z. B. „BMW 7 Series“) und dem gesprochenen Satz.
  Bilder, die etwas anderes zeigen (anderes Auto, Personen, Logos …), werden aussortiert. Das Modell wird beim
  ersten Mal einmalig geladen (ca. 1,5 GB) und läuft danach offline.
- **Beste Gesamtverteilung.** Jede Szene bekommt das insgesamt am besten passende Material. Das Titelbild zeigt
  das Thema am deutlichsten. Kein Bild kommt doppelt vor, aus Videos wird jedes Mal ein anderer Abschnitt genommen.
- **Bei Video-Links** wird genau die Stelle genommen, an der der Satz gesagt wird.
- **Nur Bilder aus dem Artikel selbst.** Teaser anderer Artikel, Autorenfotos, Werbung und Logos werden ignoriert.
- **Du hast das letzte Wort.** Mit **② Footage prüfen** siehst du pro Szene das gewählte Bild und darunter alle
  gefundenen Bilder mit Nummern. Gefällt dir eins nicht, trägst du in der Szenen-Tabelle unter **„Footage-Nr.“**
  eine andere Nummer ein. Leer lassen heißt, die KI wählt.
- **Nur das reine Bild.** Eingeblendete Texte, Untertitel, Wasserzeichen, Kanalnamen und schwarze Balken werden
  erkannt und weggeschnitten oder retuschiert, sonst wird das Bild nicht verwendet.
- **Quellen:** deine Links und Uploads, Bilder aus Artikeln, dann freies Material (YouTube mit
  Creative-Commons-Lizenz, Wikimedia Commons, Openverse).

> ⚠️ **Wichtig für das TikTok-Creator-Programm:** TikTok bezahlt nur **eigene, originale Inhalte**. Videos, die
> größtenteils aus fremden TikTok- oder YouTube-Clips bestehen, können als „nicht original“ eingestuft werden,
> auch ohne Wasserzeichen. Am sichersten sind eigene Aufnahmen, Pressematerial der Hersteller und freie
> Lizenzen. Die Option „Auch YouTube-Videos ohne freie Lizenz“ unter Einstellungen ist deshalb standardmäßig aus.

## Schnell

Das Video wird parallel auf allen Prozessorkernen gerendert. Auf einem 4-Kern-PC dauert eine Minute Video
etwa 40–50 Sekunden, mit mehr Kernen entsprechend weniger. Beim ersten Video mit einem Video-Link ohne
Untertitel wird einmalig die Spracherkennung geladen (ca. 500 MB).

## Wenn ein Link nicht geht

Die Fehlermeldung sagt jetzt genau, was los ist. Häufig:
- **YouTube „kein Bot“ / Login nötig:** YouTube einmal in Firefox öffnen (eingeloggt) und unter ⚙️ Einstellungen
  den Login-Browser „firefox“ wählen.
- Der Video-Downloader aktualisiert sich bei jedem Start automatisch, weil YouTube & Co. sich oft ändern.

## Optionale Verbesserungen

- **Bessere Scripts (lokale KI, kostenlos):** [Ollama](https://ollama.com/download) installieren und einmal in der
  Eingabeaufforderung `ollama pull qwen2.5:7b` ausführen. CarPlug Studio nutzt die KI dann automatisch.
  Empfohlen sind 16 GB RAM.
- **Eigene Stimme klonen:** **`Stimme-klonen-installieren.bat`** ausführen (lädt ca. 3–4 GB), dann im Tab
  „Stimme klonen“ 20–30 Sekunden aufnehmen. Das läuft lokal mit Chatterbox (MIT-Lizenz, kommerziell nutzbar).
  Mit Nvidia-Grafikkarte geht es schnell, ohne dauert es einige Minuten pro Video.
- **Instagram und andere Links mit Login:** Unter „Einstellungen“ den Browser wählen, in dem du eingeloggt bist.
  Firefox funktioniert am zuverlässigsten.
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
