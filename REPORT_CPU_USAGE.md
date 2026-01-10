# Report: Utilizzo Anomalo CPU durante Screenshot/OCR

**Data**: 2026-01-10
**Progetto**: OpenReLife
**Problema**: Utilizzo CPU al 600+% durante acquisizione screenshot o elaborazione OCR

---

## 1. Sommario Esecutivo

L'analisi del codebase ha identificato **tre cause principali** dell'elevato utilizzo CPU:

1. **OCR con PyTorch su CPU** - Inferenza di modelli deep learning senza accelerazione GPU
2. **Elaborazione immagini a piena risoluzione** - Nessun downsampling prima dell'OCR
3. **Pipeline sincrona e bloccante** - Tutte le operazioni eseguite in sequenza nel thread principale

L'utilizzo del 600% indica che **6+ core CPU** sono impegnati contemporaneamente, coerente con l'inferenza PyTorch multi-thread.

---

## 2. Architettura del Sistema

### 2.1 Flusso di Elaborazione Screenshot

```
take_screenshots() ──► is_similar() ──► extract_text_from_image() ──► get_embedding() ──► insert_entry()
       │                    │                     │                        │
       ▼                    ▼                     ▼                        ▼
   mss library         MSSIM calc           DocTR OCR            SentenceTransformer
   (veloce)           (numpy ops)        (PyTorch CPU)           (PyTorch CPU)
```

### 2.2 File Coinvolti

| File | Funzione | Impatto CPU |
|------|----------|-------------|
| `openrelife/screenshot.py` | Cattura e processing | **ALTO** |
| `openrelife/ocr.py` | OCR con DocTR | **MOLTO ALTO** |
| `openrelife/nlp.py` | Text embeddings | **MEDIO-ALTO** |

---

## 3. Analisi Dettagliata delle Cause

### 3.1 OCR con DocTR (Causa Principale)

**File**: `openrelife/ocr.py:1-34`

```python
ocr = ocr_predictor(
    pretrained=True,
    det_arch="db_mobilenet_v3_large",    # Detection model
    reco_arch="crnn_mobilenet_v3_large",  # Recognition model
)

def extract_text_from_image(image):
    result = ocr([image])  # Full inference su immagine a piena risoluzione
    ...
```

**Problema**:
- Utilizza **due modelli PyTorch** (detection + recognition)
- PyTorch su CPU utilizza tutti i core disponibili via OpenMP/MKL
- L'immagine viene processata a **piena risoluzione** (es. 2560x1440 = 3.6M pixel)
- Ogni chiamata esegue ~100ms-2s di inferenza intensiva

**Perché 600% CPU**:
- PyTorch imposta `OMP_NUM_THREADS` al numero di core disponibili
- Su una macchina 6-core/12-thread, può facilmente saturare tutti i thread
- MobileNetV3 + CRNN = operazioni di convoluzione massivamente parallele

### 3.2 Calcolo MSSIM (Causa Secondaria)

**File**: `openrelife/screenshot.py:20-50`

```python
def mean_structured_similarity_index(img1, img2, L=255):
    img1_gray = rgb2gray(img1)        # 3 operazioni su array completo
    img2_gray = rgb2gray(img2)        # 3 operazioni su array completo
    mu1 = np.mean(img1_gray)          # Scansione completa
    mu2 = np.mean(img2_gray)          # Scansione completa
    sigma1_sq = np.var(img1_gray)     # Scansione completa
    sigma2_sq = np.var(img2_gray)     # Scansione completa
    sigma12 = np.mean((img1_gray - mu1) * (img2_gray - mu2))  # 3 scansioni
    ...
```

**Problema**:
- **8+ operazioni** su array di ~3.6M elementi ciascuna
- Eseguito **ogni 3 secondi** (intervallo screenshot)
- Nessun downsampling per il confronto
- NumPy usa parallelismo via BLAS/OpenBLAS

### 3.3 Text Embedding (Causa Terziaria)

**File**: `openrelife/nlp.py:31-70`

```python
def get_embedding(text: str) -> np.ndarray:
    model = get_model()  # SentenceTransformer all-MiniLM-L6-v2
    ...
    sentence_embeddings = model.encode(sentences)  # PyTorch inference
```

**Problema**:
- Altro modello PyTorch (transformer 384-dim)
- Eseguito dopo ogni OCR con cambiamenti rilevati
- Contribuisce all'utilizzo CPU cumulativo

---

## 4. Misurazione dell'Impatto

### 4.1 Stima Tempi per Screenshot (su CPU tipica)

| Operazione | Tempo Stimato | CPU Cores |
|------------|---------------|-----------|
| Screenshot (mss) | ~10-50ms | 1 |
| MSSIM comparison | ~50-200ms | 2-4 |
| OCR (DocTR) | **500ms-2s** | **4-8** |
| Embedding | ~50-100ms | 2-4 |
| Save WebP | ~20-50ms | 1 |

**Totale**: ~700ms-2.5s per screenshot con cambiamenti

### 4.2 Calcolo Utilizzo CPU

Con intervallo di 3 secondi e screenshot che cambia:
- **Tempo elaborazione**: ~1.5s
- **Tempo idle**: ~1.5s
- **Duty cycle**: 50%
- **Core utilizzati**: 6+
- **CPU% medio**: 50% × 600% = **300% medio**, picchi a **600+%**

---

## 5. Alternative e Soluzioni Proposte

### 5.1 Ottimizzazione OCR

#### Opzione A: Downsampling Prima dell'OCR
**Impatto**: Riduzione 50-75% utilizzo CPU
**Complessità**: Bassa

```python
# Ridurre risoluzione a max 1280x720 prima di OCR
MAX_OCR_WIDTH = 1280
if image.shape[1] > MAX_OCR_WIDTH:
    scale = MAX_OCR_WIDTH / image.shape[1]
    new_size = (int(image.shape[1] * scale), int(image.shape[0] * scale))
    image = cv2.resize(image, new_size)
```

**Pro**: Semplice, mantiene qualità OCR accettabile
**Contro**: Possibile degradazione su testo piccolo

#### Opzione B: OCR Engine Più Leggero
**Impatto**: Riduzione 70-90% utilizzo CPU
**Complessità**: Media

| Engine | CPU Usage | Accuratezza | Note |
|--------|-----------|-------------|------|
| DocTR (attuale) | Alta | Ottima | Deep learning |
| **Tesseract OCR** | Media | Buona | C++ nativo |
| **EasyOCR** | Alta | Ottima | Simile a DocTR |
| **PaddleOCR** | Media | Ottima | Ottimizzato |
| **Apple Vision (macOS)** | Bassa | Ottima | Usa Neural Engine |
| **Windows.Media.Ocr** | Bassa | Buona | GPU accelerato |

**Raccomandazione**: Utilizzare API native del sistema operativo quando disponibili.

#### Opzione C: Limitare Utilizzo Thread PyTorch
**Impatto**: Riduzione 30-50% picco CPU
**Complessità**: Bassa

```python
import torch
torch.set_num_threads(2)  # Limita a 2 thread
```

**Pro**: Immediato, nessuna modifica architetturale
**Contro**: Aumenta tempo elaborazione

### 5.2 Ottimizzazione MSSIM

#### Opzione D: Downsampling per Comparazione
**Impatto**: Riduzione 80% tempo MSSIM
**Complessità**: Bassa

```python
def is_similar_fast(img1, img2, scale=0.25):
    h, w = img1.shape[:2]
    small1 = cv2.resize(img1, (int(w*scale), int(h*scale)))
    small2 = cv2.resize(img2, (int(w*scale), int(h*scale)))
    return mean_structured_similarity_index(small1, small2) >= 0.9
```

#### Opzione E: Hash Percettivo (pHash)
**Impatto**: Riduzione 95% tempo comparazione
**Complessità**: Bassa

```python
import imagehash
from PIL import Image

def is_similar_phash(img1, img2, threshold=5):
    hash1 = imagehash.phash(Image.fromarray(img1))
    hash2 = imagehash.phash(Image.fromarray(img2))
    return hash1 - hash2 < threshold
```

### 5.3 Architettura Asincrona

#### Opzione F: Processing in Processo Separato
**Impatto**: Elimina blocco UI, distribuisce carico
**Complessità**: Alta

```python
from multiprocessing import Process, Queue

def ocr_worker(queue_in, queue_out):
    while True:
        image = queue_in.get()
        text, coords = extract_text_from_image(image)
        queue_out.put((text, coords))

# Main thread invia immagini, riceve risultati async
```

#### Opzione G: Rate Limiting Adattivo
**Impatto**: Riduzione 50-70% utilizzo medio
**Complessità**: Media

```python
import time

last_ocr_time = 0
MIN_OCR_INTERVAL = 5  # Minimo 5 secondi tra OCR

def should_run_ocr():
    global last_ocr_time
    if time.time() - last_ocr_time < MIN_OCR_INTERVAL:
        return False
    last_ocr_time = time.time()
    return True
```

---

## 6. Matrice Decisionale

| Soluzione | Riduzione CPU | Complessità | Tempo Impl. | Raccomandazione |
|-----------|---------------|-------------|-------------|-----------------|
| A. Downsampling OCR | 50-75% | Bassa | 1h | **Priorità 1** |
| D. Downsampling MSSIM | 80% (MSSIM) | Bassa | 30min | **Priorità 1** |
| C. Limit PyTorch threads | 30-50% | Bassa | 10min | **Priorità 1** |
| E. pHash invece di MSSIM | 95% (MSSIM) | Bassa | 1h | Priorità 2 |
| B. OCR nativo OS | 70-90% | Media | 4-8h | Priorità 2 |
| G. Rate limiting | 50-70% | Media | 2h | Priorità 3 |
| F. Multiprocessing | Variabile | Alta | 8-16h | Priorità 4 |

---

## 7. Raccomandazioni Immediate

### Quick Wins (implementabili subito)

1. **Limitare thread PyTorch** - Aggiungere all'inizio di `screenshot.py`:
   ```python
   import torch
   torch.set_num_threads(2)
   ```

2. **Downsampling MSSIM** - Modificare `is_similar()` per usare immagini ridotte

3. **Aumentare intervallo minimo** - Cambiare `screenshot_interval` default da 3 a 5 secondi

### Medio Termine

4. **Downsampling OCR** - Ridurre risoluzione prima di chiamare DocTR

5. **Caching modelli** - Verificare che i modelli non vengano ricaricati

### Lungo Termine

6. **Migrazione a OCR nativo** - Usare Apple Vision Framework su macOS

7. **Architettura asincrona** - Separare cattura da processing

---

## 8. Conclusioni

L'utilizzo CPU al 600% è causato principalmente dall'**inferenza OCR con PyTorch** che utilizza tutti i core disponibili. Le soluzioni immediate più efficaci sono:

1. Limitare i thread PyTorch a 2
2. Ridurre la risoluzione delle immagini prima del processing
3. Usare hash percettivi invece di MSSIM

Queste modifiche possono ridurre l'utilizzo CPU del **60-80%** con minimo impatto sulla qualità del riconoscimento testo.

---

## Appendice: Dipendenze Rilevanti

```
torch==2.2.2/2.6.0     - Deep learning framework
python-doctr           - OCR engine (PyTorch backend)
sentence-transformers  - Text embedding model
numpy==1.26.4          - Operazioni numeriche
mss==9.0.1             - Screenshot capture
Pillow==10.3.0         - Image processing
```
