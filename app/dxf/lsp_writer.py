"""
LspWriter — generates a self-contained AutoCAD link file.

Protection model
----------------
* Your DWG blocks are embedded as binary inside block_data.py (part of the exe).
* At link-generation time the exe writes them to  %USERPROFILE%\\MTAP\\blocks\\
* The LISP loads them with INSERT — AutoCAD reads your exact geometry.
* The link file is overwritten on each export; nothing meaningful sits on disk
  between uses.  All logic lives in compiled Python bytecode inside the exe.
"""

import math
import os

from app.engine.tools.drill import DrillBlankParams
from app.dxf.block_data import BLOCKS as _BLOCK_DATA

EPS = 1e-6

# ── block reference text heights (set by the user when drawing the blocks) ────
# Each block is inserted at  scale = TARGET / REF_HEIGHT  so its text renders at
# the same on-drawing height regardless of how big/small it was originally drawn.
_BT_REF_H  = 3.3080   # BACKTAPER — MTEXT "BACK TAPER" label height
_GDT_REF_H = 0.2500   # GDT       — TEXT (VAL attribute) height
_DAT_REF_H = 0.2652   # DATUM     — TEXT ("A") height

# Block text height as a MULTIPLE of the dimension text height.
# Drafting logic: every annotation on the drawing shares ONE text height that
# scales with the tool size.  1.0 => block text == dimension text.
# Nudge down for smaller blocks, up for bigger.
_BLOCK_TXT_RATIO = 0.75

# Native (scale-1) width of the GD&T frame, measured from the user's block
# (diagnostic reported 53.55 mm at scale 18 => 53.55/18).  Used to CENTER the
# frame over the Dc dimension.  Re-measure if the GDT block is redrawn.
_GDT_NATIVE_W = 53.55 / 18.0

# Source block DWGs on the dev machine.  When these exist we point DMTAP at
# them directly so edits show up instantly (no re-embed needed).  On a shipped
# exe they won't exist, so we fall back to the embedded copies (protected).
_SOURCE_BLOCKS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "autocad", "blocks"))

# ══════════════════════════════════════════════════════════════════════════════
#  EMBEDDED LISP LIBRARY
# ══════════════════════════════════════════════════════════════════════════════
_LIBRARY = r"""
;; MTAP drawing library (auto-generated)
(vl-load-com)

;; layer helper
(defun MTAP:make-layer (name color ltype / ent ed)
  (if (tblsearch "LAYER" name)
    (vl-catch-all-apply
      (function (lambda ()
        (setq ed (entget (tblobjname "LAYER" name)))
        (entmod (subst (cons 62 color) (assoc 62 ed) ed)))))
    (progn
      (if (and ltype (/= (strcase ltype) "CONTINUOUS")
               (not (tblsearch "LTYPE" ltype)))
        (vl-catch-all-apply
          (function (lambda () (command "_.LINETYPE" "_Load" ltype "acad" "")))))
      (entmake
        (list '(0 . "LAYER") '(100 . "AcDbSymbolTableRecord")
              '(100 . "AcDbLayerTableRecord")
              (cons 2 name) '(70 . 0) (cons 62 color)
              (cons 6 (if (tblsearch "LTYPE" ltype) ltype "Continuous"))))))
  name)

;; dim variable setup
(defun MTAP:setvars ()
  (setvar "DIMTXT"  MTAP:TXT) (setvar "DIMASZ"  MTAP:TXT)
  (setvar "DIMEXE"  (* MTAP:TXT 0.5)) (setvar "DIMEXO" (* MTAP:TXT 0.8))
  (setvar "DIMGAP"  (* MTAP:TXT 0.35)) (setvar "DIMDEC" 2)
  (setvar "DIMADEC" 0) (setvar "DIMTAD" 1) (setvar "DIMTIH" 1)
  (setvar "DIMTOH"  1) (setvar "DIMCLRD" 1) (setvar "DIMCLRE" 1)
  (setvar "DIMCLRT" 2) (setvar "DIMDSEP" ".")
  ;; CRITICAL: disable block unit auto-scaling so OUR insert scale is the
  ;; only scaling applied.  Without this, a units mismatch between the block
  ;; DWG and the drawing silently rescales blocks and ignores our factor.
  (setvar "INSUNITS" 0)            ; 0 = unitless => no auto-scale on insert
  (vl-catch-all-apply (function (lambda () (setvar "INSUNITSDEFSOURCE" 0))))
  (vl-catch-all-apply (function (lambda () (setvar "INSUNITSDEFTARGET" 0)))))

;; measure the on-drawing width of an entity's bounding box (diagnostic)
(defun MTAP:bbox-w (e / o mn mx)
  (setq o (vlax-ename->vla-object e))
  (vl-catch-all-apply
    (function (lambda () (vla-getboundingbox o 'mn 'mx))))
  (if (and mn mx)
    (- (car (vlax-safearray->list mx)) (car (vlax-safearray->list mn)))
    0.0))

;; force CENTER linetype on the last-created entity
(defun MTAP:set-center (/ ent ed)
  (vl-catch-all-apply
    (function (lambda ()
      (setq ent (entlast) ed (entget ent))
      (if (assoc 6 ed)
        (setq ed (subst '(6 . "CENTER") (assoc 6 ed) ed))
        (setq ed (append ed '((6 . "CENTER")))))
      (setq ed (if (assoc 48 ed)
                 (subst (cons 48 MTAP:LTSCALE) (assoc 48 ed) ed)
                 (append ed (list (cons 48 MTAP:LTSCALE)))))
      (entmod ed)))))

;; entity-after helper — returns first entity created after 'prev'
;; (handles prev = nil when the drawing was empty before the insert)
(defun MTAP:next-ent (prev)
  (if prev (entnext prev) (entnext)))

;; FORCE-redefine a block from its DWG file, every run.
;; The  name=path  syntax tells AutoCAD to (re)define the block from the
;; external file even if a block of that name already exists — this wipes
;; any stale/cached definition so we ALWAYS use the user's real DWG.
;; If the block already exists AutoCAD asks "Redefine it? [Yes/No]" — we
;; feed _Yes only in that case.  A dummy insert is placed at the origin and
;; immediately erased; the refreshed block DEFINITION stays in the drawing.
(defun MTAP:load-block (name fpath / exists prev ins)
  (setq exists (tblsearch "BLOCK" name)
        prev   (entlast))
  (setvar "ATTDIA" 0)
  (setvar "ATTREQ" 0)
  (if exists
    (command "_.-INSERT" (strcat name "=" fpath) "_Yes" "0,0" 1 1 0)
    (command "_.-INSERT" (strcat name "=" fpath)        "0,0" 1 1 0))
  (setq ins (MTAP:next-ent prev))
  (if (and ins (= (cdr (assoc 0 (entget ins))) "INSERT"))
    (command "_.ERASE" ins "")))   ; erasing the INSERT keeps the block def

;; insert an already-defined block by name — returns the INSERT entity.
;; Uses -INSERT (command-line form, no dialog) and (next-ent prev) so we get
;; the INSERT itself, not SEQEND (which would break set-attrib).
(defun MTAP:ins-block (name ip sc / prev)
  (setq prev (entlast))
  (setvar "ATTDIA" 0)
  (setvar "ATTREQ" 0)
  (command "_.-INSERT" name ip sc sc 0)
  (MTAP:next-ent prev))   ; the INSERT entity

;; walk INSERT -> ATTRIB* -> SEQEND and update the named attribute
(defun MTAP:set-attrib (blk tag val / e ed)
  (setq e (entnext blk))   ; first ATTRIB (blk must be the INSERT entity)
  (while (and e (not (= (cdr (assoc 0 (entget e))) "SEQEND")))
    (setq ed (entget e))
    (if (and (= (cdr (assoc 0 ed)) "ATTRIB")
             (= (cdr (assoc 2 ed)) (strcase tag)))
      (progn (entmod (subst (cons 1 val) (assoc 1 ed) ed)) (entupd e)))
    (setq e (entnext e))))

;; ── TEMPLATE: center the drawing inside the customer title-block window ───────
;; Union the bounding boxes of every entity created AFTER 'startent' (the whole
;; tool drawing: outline, dims, annotation blocks, note).  Returns (mn mx) or nil.
(defun MTAP:range-bbox (startent / e o lo hi mn mx)
  (setq e (if startent (entnext startent) (entnext)))
  (while e
    (setq o (vlax-ename->vla-object e))
    (if (not (vl-catch-all-error-p
               (vl-catch-all-apply 'vla-getboundingbox (list o 'lo 'hi))))
      (progn
        (setq lo (vlax-safearray->list lo)
              hi (vlax-safearray->list hi))
        (if mn (setq mn (list (min (car mn) (car lo)) (min (cadr mn) (cadr lo))))
               (setq mn (list (car lo) (cadr lo))))
        (if mx (setq mx (list (max (car mx) (car hi)) (max (cadr mx) (cadr hi))))
               (setq mx (list (car hi) (cadr hi))))))
    (setq e (entnext e)))
  (if (and mn mx) (list mn mx) nil))

;; Find the drawing-area rectangle inside a block DEFINITION by layer.
;; Scans the block's sub-entities for anything on layer MTAP_WINDOW and unions
;; its vertices (handles both LWPOLYLINE group-10 and LINE group-10/11).
;; Returns (mn mx) in block-local coords, or nil.
(defun MTAP:block-window (bname / e ed mn mx)
  (setq e (cdr (assoc -2 (tblsearch "BLOCK" bname))))
  (while e
    (setq ed (entget e))
    (if (= (strcase (cdr (assoc 8 ed))) "MTAP_WINDOW")
      (foreach pr ed
        (if (or (= 10 (car pr)) (= 11 (car pr)))
          (progn
            (if mn (setq mn (list (min (car mn) (cadr pr)) (min (cadr mn) (caddr pr))))
                   (setq mn (list (cadr pr) (caddr pr))))
            (if mx (setq mx (list (max (car mx) (cadr pr)) (max (cadr mx) (caddr pr))))
                   (setq mx (list (cadr pr) (caddr pr))))))))
    (setq e (entnext e)))
  (if (and mn mx) (list mn mx) nil))

;; Fallback: overall extents of the whole block DEFINITION (any layer), unioning
;; every sub-entity vertex (group 10/11).  Used when MTAP_WINDOW isn't found so
;; the template is still inserted and roughly centered instead of vanishing.
(defun MTAP:block-extents (bname / e ed mn mx)
  (setq e (cdr (assoc -2 (tblsearch "BLOCK" bname))))
  (while e
    (setq ed (entget e))
    (foreach pr ed
      (if (or (= 10 (car pr)) (= 11 (car pr)))
        (progn
          (if mn (setq mn (list (min (car mn) (cadr pr)) (min (cadr mn) (caddr pr))))
                 (setq mn (list (cadr pr) (caddr pr))))
          (if mx (setq mx (list (max (car mx) (cadr pr)) (max (cadr mx) (caddr pr))))
                 (setq mx (list (cadr pr) (caddr pr)))))))
    (setq e (entnext e)))
  (if (and mn mx) (list mn mx) nil))

;; Insert MTAP_TEMPLATE scaled so its MTAP_WINDOW wraps the tool bbox with a
;; little margin, positioned so the WINDOW CENTER lands on the TOOL CENTER —
;; i.e. the drawing is centered in the rectangle both horizontally & vertically.
;; The TOOL stays true 1:1; only the TEMPLATE scales.  Returns the INSERT ename.
;; If MTAP_WINDOW isn't found we fall back to the block's overall extents so the
;; border is ALWAYS drawn (never silently skipped).
(defun MTAP:place-template (bb / wn tmn tmx tcx tcy tw th
                                  wmn wmx wcx wcy ww wh s ipx ipy margin usedwin ins)
  (setq wn (MTAP:block-window "MTAP_TEMPLATE") usedwin T)
  (if (null wn)
    (progn
      (princ "\n*** MTAP: MTAP_WINDOW layer not found — using full template extents.")
      (setq wn (MTAP:block-extents "MTAP_TEMPLATE") usedwin nil)))
  (if (and bb wn)
    (progn
      (setq tmn (car bb)  tmx (cadr bb)
            tw  (- (car tmx) (car tmn))   th  (- (cadr tmx) (cadr tmn))
            tcx (/ (+ (car tmn) (car tmx)) 2.0)
            tcy (/ (+ (cadr tmn) (cadr tmx)) 2.0)
            wmn (car wn)  wmx (cadr wn)
            ww  (- (car wmx) (car wmn))   wh  (- (cadr wmx) (cadr wmn))
            wcx (/ (+ (car wmn) (car wmx)) 2.0)
            wcy (/ (+ (cadr wmn) (cadr wmx)) 2.0)
            margin 0.70)            ; tool fills up to 70% of the window (breathing room)
      ;; scale template so the tool (incl. margin) fits in BOTH directions
      (setq s (max (/ tw (* ww margin)) (/ th (* wh margin))))
      ;; window-center(world) = IP + s*window-center(local)  =>  solve for IP
      (setq ipx (- tcx (* s wcx))
            ipy (- tcy (* s wcy)))
      (princ (strcat "\n  template " (if usedwin "(window)" "(full-extents)")
                     " scale=" (rtos s 2 3)
                     "  box=" (rtos ww 2 1) "x" (rtos wh 2 1)
                     "  tool=" (rtos tw 2 1) "x" (rtos th 2 1)))
      (setvar "CLAYER" "0")
      (setq ins (MTAP:ins-block "MTAP_TEMPLATE" (list ipx ipy) s))
      ;; the MTAP_WINDOW rectangle only MARKS the drawing area — it must never
      ;; display or plot.  Turn its layer Off + No-plot (catch if absent).
      (vl-catch-all-apply
        (function (lambda ()
          (command "_.-LAYER" "_Off" "MTAP_WINDOW"
                              "_Plot" "_No" "MTAP_WINDOW" ""))))
      ins)
    (progn
      (princ "\n*** MTAP: template has no measurable geometry; border skipped.")
      nil)))

;; Fill the title-block attributes from the app's metadata and force the filled
;; values YELLOW (color 2).  Tag matching is case-insensitive with aliases, and
;; every tag found is echoed so a mismatch is obvious on the command line.
;;
;; Uses ActiveX vla-put-TextString rather than (entmod (cons 1 val)).  A MULTILINE
;; attribute (e.g. DESC) is an AcDbAttribute wrapping an embedded AcDbMText, so its
;; visible text comes from that sub-object — editing DXF group 1 changes the stored
;; tag but NOT the displayed text, which is why DESC appeared blank.  TextString
;; updates both single-line and multiline attributes correctly.
(defun MTAP:fill-template (blk / obj att tag val)
  (if blk
    (progn
      (setq obj (vlax-ename->vla-object blk))
      (if (= (vla-get-HasAttributes obj) :vlax-true)
        (foreach att (vlax-invoke obj 'GetAttributes)
          (setq tag (strcase (vla-get-TagString att))
                val (cond
                      ((member tag '("CUSTOMER" "CLIENT" "COMPANY")) MTAP:CUSTOMER)
                      ((member tag '("DRAWNBY" "DRAWN BY" "DRAWN_BY" "DRAWN" "DRWN" "BY")) MTAP:DRAWNBY)
                      ((member tag '("CHECKEDBY" "CHECKED BY" "CHECKED_BY" "CHECKED" "CHKBY" "CHK")) MTAP:CHECKEDBY)
                      ((member tag '("TITLE" "PARTNAME" "PART" "PART NAME" "NAME")) MTAP:TITLE)
                      ((member tag '("DATE")) MTAP:DATE)
                      ((member tag '("DESC" "DESCRIPTION" "REMARKS" "REMARK" "NOTES" "NOTE")) MTAP:DESC)
                      (T nil)))
          (princ (strcat "\n  attrib " tag
                         (if val (strcat " <= \"" val "\"") "  (no match — left as-is)")))
          (if val
            (progn
              (vl-catch-all-apply (function (lambda () (vla-put-TextString att val))))
              (vl-catch-all-apply (function (lambda () (vla-put-Color att 2))))
              (vl-catch-all-apply (function (lambda () (vla-update att))))))))))
  (princ))

;; main draw
(defun MTAP:draw ( / osm cme res blk)
  (setq osm (getvar "OSMODE") cme (getvar "CMDECHO"))
  (setvar "OSMODE" 0) (setvar "CMDECHO" 0)

  (setq res
    (vl-catch-all-apply
      (function (lambda ()

        ;; layers  (2=yellow  4=cyan  1=red)
        (MTAP:make-layer "MTAP-OUTLINE" 2 "Continuous")
        (MTAP:make-layer "MTAP-CENTER"  4 "CENTER")
        (MTAP:make-layer "MTAP-DIM"     1 "Continuous")
        (MTAP:make-layer "MTAP-ANNOT"   2 "Continuous")
        (MTAP:setvars)

        ;; version + scale banner — confirms you're running the latest link file
        (princ (strcat "\n=== MTAP build R13 ==="
                       "\n  block scales:  BT=" (rtos MTAP:SCALE_BT 2 2)
                       "  GDT=" (rtos MTAP:SCALE_GDT 2 2)
                       "  DAT=" (rtos MTAP:SCALE_DAT 2 2)
                       "\n  INSUNITS now = " (itoa (getvar "INSUNITS")) "\n"))

        ;; force-redefine your blocks from the extracted DWG files (every run)
        (MTAP:load-block "MTAP_BACKTAPER" MTAP:PATH_BACKTAPER)
        (MTAP:load-block "MTAP_GDT"       MTAP:PATH_GDT)
        (MTAP:load-block "MTAP_DATUM"     MTAP:PATH_DATUM)
        (MTAP:load-block "MTAP_TEMPLATE"  MTAP:PATH_TEMPLATE)

        ;; mark the start of the tool drawing so we can measure its bounding box
        ;; later (everything created after here = the drawing to center)
        (setq MTAP:TOOLSTART (entlast))

        ;; outline
        (setvar "CLAYER" "MTAP-OUTLINE")
        (apply 'command (append '("_.PLINE") MTAP:PROFILE '("_Close")))

        ;; point-angle base line (joins the cone base corners, top to bottom)
        (if MTAP:HASPTBASE
          (command "_.LINE" MTAP:PTBASE1 MTAP:PTBASE2 ""))

        ;; reinforcement transition lines: taper begin (+ taper end if a
        ;; reinforcement cone exists; otherwise a single junction line)
        (if MTAP:HASTR1 (command "_.LINE" MTAP:TR1A MTAP:TR1B ""))
        (if MTAP:HASTR2 (command "_.LINE" MTAP:TR2A MTAP:TR2B ""))

        ;; back-face chamfer root line (full shank diameter at the chamfer start)
        (if MTAP:HASBFLINE (command "_.LINE" MTAP:BF1 MTAP:BF2 ""))

        ;; centerline
        (setvar "CLAYER" "MTAP-CENTER")
        (command "_.LINE" MTAP:CL1 MTAP:CL2 "")
        (MTAP:set-center)

        ;; diameter dims
        (setvar "CLAYER" "MTAP-DIM")
        (command "_.DIMLINEAR"
                 (list MTAP:XPB MTAP:RC) (list MTAP:XPB (- MTAP:RC))
                 "_Text" (strcat "%%c" MTAP:DCSTR) (list MTAP:DCDIMX 0.0))
        (if MTAP:HASD
          (command "_.DIMLINEAR"
                   (list MTAP:XEND MTAP:RS) (list MTAP:XEND (- MTAP:RS))
                   "_Text" (strcat "%%c" MTAP:DSTR) (list MTAP:DDIMX 0.0)))

        ;; point angle
        (if MTAP:HASPT
          (command "_.DIMANGULAR" "" MTAP:APEX MTAP:PA1 MTAP:PA2 MTAP:PALOC))

        ;; reinforcement angle (only when a reinforcement cone exists)
        (if MTAP:HASREINF
          (command "_.DIMANGULAR" "" MTAP:RA_VERT MTAP:RA_E1 MTAP:RA_E2 MTAP:RA_LOC))

        ;; length dims
        (if MTAP:HASFL
          (command "_.DIMLINEAR" MTAP:FL1 MTAP:FL2 "_Horizontal"
                   (list (/ (+ (car MTAP:FL1) (car MTAP:FL2)) 2.0) MTAP:NEAR)))
        (command "_.DIMLINEAR" MTAP:LS1 MTAP:LS2 "_Horizontal"
                 (list (/ (+ (car MTAP:LS1) (car MTAP:LS2)) 2.0) MTAP:NEAR))
        (command "_.DIMLINEAR" MTAP:OAL1 MTAP:OAL2 "_Horizontal"
                 (list (/ (+ (car MTAP:OAL1) (car MTAP:OAL2)) 2.0) MTAP:FAR))

        ;; GD&T — frame above Dc dim (leader from bottom-left corner) + datum
        ;; block floated above the shank with its own leader to the surface
        (setvar "CLAYER" "MTAP-DIM")
        (if MTAP:HASGDT
          (progn
            (setq blk (MTAP:ins-block "MTAP_GDT" MTAP:GDTINS MTAP:SCALE_GDT))
            (MTAP:set-attrib blk "VAL" MTAP:GDTVAL)
            (command "_.LINE" MTAP:GDT_LDR1 MTAP:GDT_LDR2 "")
            (MTAP:ins-block "MTAP_DATUM" MTAP:DATINS MTAP:SCALE_DAT)
            (command "_.LINE" MTAP:DAT_LDR1 MTAP:DAT_LDR2 "")))

        ;; back taper — block on annot layer, leader on DIM layer (red)
        (if MTAP:HASBT
          (progn
            (setvar "CLAYER" "MTAP-ANNOT")
            (setq blk (MTAP:ins-block "MTAP_BACKTAPER" MTAP:BTINS MTAP:SCALE_BT))
            (MTAP:set-attrib blk "VAL" MTAP:BTVAL)
            (setvar "CLAYER" "MTAP-DIM")   ; red leader
            (command "_.LINE" MTAP:BT_LDR1 MTAP:BT_LDR2 "")))

        ;; general note (yellow annotation layer)
        (setvar "CLAYER" "MTAP-ANNOT")
        (command "_.TEXT" "_Justify" "_Middle"
                 MTAP:NOTEPT (* MTAP:TXT 0.9) 0 MTAP:NOTE)

        ;; ---- customer template: border + title block, scaled to wrap the
        ;; drawing and positioned so the tool is centered in the window ----
        (setq blk (MTAP:place-template (MTAP:range-bbox MTAP:TOOLSTART)))
        (MTAP:fill-template blk)

        (command "_.ZOOM" "_Extents")
        "ok"))))

  (setvar "OSMODE" osm) (setvar "CMDECHO" cme)
  (if (vl-catch-all-error-p res)
    (progn (princ "\n*** MTAP ERROR: ")
           (princ (vl-catch-all-error-message res))
           (princ "\n    Please report this message."))
    (princ "\nMTAP: drawing complete."))
  (princ))
;; end library
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Python helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v: float) -> str:
    return f"{v:.6f}"

def _pt(x: float, y: float) -> str:
    return f"(list {_num(x)} {_num(y)})"

def _bool(b: bool) -> str:
    return "T" if b else "nil"

def _lsp_path(p: str) -> str:
    return p.replace("\\", "/")


def _lstr(s) -> str:
    """Quote a Python string as an AutoLISP string literal (escapes \\ and ").

    Newlines (only possible from the multiline Description box) are converted to
    the MTEXT paragraph code \\P so they (a) never break the .lsp file with a raw
    newline inside a string literal and (b) render as real line breaks in the
    multiline DESC attribute.  Single-line fields never contain newlines.
    """
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\\\P")
    return f'"{s}"'


def _chamfer_backface(pts, x_end, rs, c):
    """
    Replace the two sharp back-face corners (x_end, +/-rs) with a 45-degree
    chamfer of size c.  The chamfer eats axially into the shank; the back face
    stays at x_end.  Returns a new point list preserving traversal order.
        (x_end, rs)  -> (x_end - c, rs), (x_end, rs - c)
        (x_end,-rs)  -> (x_end, -(rs - c)), (x_end - c, -rs)
    """
    out = []
    for (x, y) in pts:
        if abs(x - x_end) < EPS and abs(y - rs) < EPS:
            out.append((x_end - c, rs))
            out.append((x_end, rs - c))
        elif abs(x - x_end) < EPS and abs(y + rs) < EPS:
            out.append((x_end, -(rs - c)))
            out.append((x_end - c, -rs))
        else:
            out.append((x, y))
    return out


class LspWriter:
    def __init__(self, params: DrillBlankParams, meta: dict | None = None):
        self.params  = params
        self.meta    = meta or {}
        p            = params
        self.feature = max(p.overall_length, p.cutting_diameter * 4.0,
                           p.shank_diameter * 4.0, 1.0)
        self.txt     = self.feature * 0.022
        self.gap     = self.feature * 0.055

    def generate(self, link_path: str) -> None:
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        blocks_dir = self._resolve_blocks_dir(os.path.dirname(link_path))
        with open(link_path, "w", encoding="utf-8") as fh:
            fh.write(self.build(blocks_dir))

    @staticmethod
    def _resolve_blocks_dir(dest_dir: str) -> str:
        """
        Pick where DMTAP reads the block DWGs from.
        Dev machine: use the live source folder so block edits apply instantly.
        Shipped exe: extract the embedded copies to a temp folder.
        """
        src = _SOURCE_BLOCKS_DIR
        if all(os.path.exists(os.path.join(src, f"{n}.dwg")) for n in _BLOCK_DATA):
            return src
        return LspWriter._extract_blocks(dest_dir)

    @staticmethod
    def _extract_blocks(dest_dir: str) -> str:
        """Extract embedded DWG blocks to dest_dir/blocks/ and return the path."""
        out = os.path.join(dest_dir, "blocks")
        os.makedirs(out, exist_ok=True)
        for name, data in _BLOCK_DATA.items():
            path = os.path.join(out, f"{name}.dwg")
            with open(path, "wb") as f:
                f.write(data)
        return out

    def build(self, blocks_dir: str = "") -> str:
        lines: list[str] = [_LIBRARY]
        a    = lines.append
        p    = self.params
        rc   = p.cutting_diameter / 2.0
        rs   = p.shank_diameter   / 2.0
        rmax = max(rc, rs)
        gap  = self.gap
        txt  = self.txt

        # block file paths (forward slashes for AutoLISP)
        bd = _lsp_path(blocks_dir) if blocks_dir else "."
        a(";; block paths — extracted from exe at link-generation time")
        a(f'(setq MTAP:PATH_BACKTAPER "{bd}/MTAP_BACKTAPER.dwg")')
        a(f'(setq MTAP:PATH_GDT       "{bd}/MTAP_GDT.dwg")')
        a(f'(setq MTAP:PATH_DATUM      "{bd}/MTAP_DATUM.dwg")')
        a(f'(setq MTAP:PATH_TEMPLATE   "{bd}/MTAP_TEMPLATE.dwg")')
        a("")

        # title-block / template metadata (fills the customer template later)
        m = self.meta
        title = (f"{p.tool_type.upper()} BLANK  "
                 f"Dc{p.cutting_diameter:g} x D{p.shank_diameter:g} x "
                 f"OAL{p.overall_length:g}")
        a(";; drawing metadata — feeds the template title block")
        a(f"(setq MTAP:CUSTOMER  {_lstr(m.get('customer', ''))})")
        a(f"(setq MTAP:DRAWNBY   {_lstr(m.get('drawn_by', ''))})")
        a(f"(setq MTAP:CHECKEDBY {_lstr(m.get('checked_by', ''))})")
        a(f"(setq MTAP:DESC      {_lstr(m.get('description', ''))})")
        a(f"(setq MTAP:TITLE     {_lstr(m.get('title') or title)})")
        a(f"(setq MTAP:DATE      {_lstr(m.get('date', ''))})")
        a("")

        # per-block insertion scales  (scale = block_txt_target / block_ref_height)
        # Logic: block text height = dimension text height * ratio, so ALL text
        # on the drawing is one consistent size that scales with the tool.
        block_txt = self.txt * _BLOCK_TXT_RATIO
        a(f";; insertion scales — block text = dim text * {_BLOCK_TXT_RATIO}"
          f" = {block_txt:.3f} mm")
        a(f"(setq MTAP:SCALE_BT  {_num(block_txt / _BT_REF_H)})")
        a(f"(setq MTAP:SCALE_GDT {_num(block_txt / _GDT_REF_H)})")
        a(f"(setq MTAP:SCALE_DAT {_num(block_txt / _DAT_REF_H)})")
        a("")

        a(f";; {p.tool_type}  Dc={p.cutting_diameter:g}  D={p.shank_diameter:g}"
          f"  OAL={p.overall_length:g}  PA={p.point_angle:g}")
        a(f"(setq MTAP:TXT     {_num(txt)})")
        a(f"(setq MTAP:LTSCALE {_num(max(rmax * 0.6, 0.5))})")
        a("")

        # profile — with a 45-deg back-face chamfer = 0.1 * shank diameter
        chamfer = 0.1 * p.shank_diameter
        prof    = _chamfer_backface(p.profile_points(), p.x_end, rs, chamfer)
        pts     = " ".join(_pt(x, y) for x, y in prof)
        a(f"(setq MTAP:PROFILE (list {pts}))")
        a("")

        # back-face chamfer "root" line — a straight vertical line at the chamfer
        # start (full shank diameter), connecting the top & bottom chamfer corners
        # so the back-face edge reads clearly, like the other transition lines.
        has_bf = chamfer > EPS
        a(f"(setq MTAP:HASBFLINE {_bool(has_bf)})")
        if has_bf:
            a(f"(setq MTAP:BF1 {_pt(p.x_end - chamfer,  rs)})")
            a(f"(setq MTAP:BF2 {_pt(p.x_end - chamfer, -rs)})")
        else:
            a("(setq MTAP:BF1 nil MTAP:BF2 nil)")
        a("")

        # centerline — trimmed to a small nub past each end so it doesn't run
        # into the diameter dimensions / point-angle annotation
        a(f"(setq MTAP:CL1 {_pt(-(txt * 0.8), 0.0)})")
        a(f"(setq MTAP:CL2 {_pt(p.overall_length + txt * 1.2, 0.0)})")
        a("")

        # point angle
        has_pt = p.point_length > EPS
        a(f"(setq MTAP:HASPT {_bool(has_pt)})")
        if has_pt:
            cone_diag = math.hypot(p.point_length, rc)
            radius    = min(cone_diag * 0.75, p.point_length * 1.6, self.feature * 0.11)
            radius    = max(radius, txt * 2.0)
            radius    = min(radius, rc * 0.80)
            self._par = radius
            a(f"(setq MTAP:APEX  {_pt(0.0, 0.0)})")
            a(f"(setq MTAP:PA1   {_pt(p.x_point_base, -rc)})")
            a(f"(setq MTAP:PA2   {_pt(p.x_point_base,  rc)})")
            # arc/label location lifted ABOVE the centerline so it doesn't sit on it
            a(f"(setq MTAP:PALOC {_pt(radius, rc * 0.55)})")
        else:
            self._par = 0.0
            a("(setq MTAP:APEX nil MTAP:PA1 nil MTAP:PA2 nil MTAP:PALOC nil)")
        a("")

        # point-angle base line: straight line joining the two base corners of
        # the cone (top to bottom) at the point base
        a(f"(setq MTAP:HASPTBASE {_bool(has_pt)})")
        if has_pt:
            a(f"(setq MTAP:PTBASE1 {_pt(p.x_point_base,  rc)})")
            a(f"(setq MTAP:PTBASE2 {_pt(p.x_point_base, -rc)})")
        else:
            a("(setq MTAP:PTBASE1 nil MTAP:PTBASE2 nil)")
        a("")

        # transition lines for the shank reinforcement.
        # With a reinforcement cone: one line where the taper BEGINS (body end)
        # and one where it ENDS (shank start).  Without reinforcement those two
        # x's are identical, so a single straight line marks the step/junction.
        x_be   = p.x_body_end
        x_ss   = p.x_shank_start
        reinf  = p.reinforcement_length > EPS
        a(f"(setq MTAP:HASTR1 T)")
        a(f"(setq MTAP:TR1A {_pt(x_be,  rc)})")          # taper begin (body radius)
        a(f"(setq MTAP:TR1B {_pt(x_be, -rc)})")
        a(f"(setq MTAP:HASTR2 {_bool(reinf)})")
        if reinf:
            a(f"(setq MTAP:TR2A {_pt(x_ss,  rs)})")       # taper end (shank radius)
            a(f"(setq MTAP:TR2B {_pt(x_ss, -rs)})")
        else:
            # no reinforcement: single full-height line at the junction
            a(f"(setq MTAP:TR1A {_pt(x_be,  rmax)})")
            a(f"(setq MTAP:TR1B {_pt(x_be, -rmax)})")
            a("(setq MTAP:TR2A nil MTAP:TR2B nil)")
        a("")

        # reinforcement angle — native angular dimension reading the cone's
        # angle FROM the axis (= the reinforcement angle the user entered).
        # Vertex at taper-begin (body top); ray 1 horizontal (axis-parallel),
        # ray 2 up the cone edge to taper-end.  Both rays point the same way so
        # the measured angle is the acute cone angle, not its reflex.
        a(f"(setq MTAP:HASREINF {_bool(reinf)})")
        if reinf:
            # Place the dimension-arc label on the TRUE bisector of the two rays
            # (vertex->E1 and vertex->E2).  The bisector always lies in the minor
            # (<=180 deg) wedge, so DIMANGULAR reports the actual cone angle (30)
            # instead of its reflex (330) regardless of whether the cone steps up
            # or down.  Using a fixed +half-angle direction was the 330-deg bug.
            r_arc = max((x_ss - x_be) * 2.5, gap * 3.0)
            d1x, d1y = (x_ss - x_be), (rc - rc)      # vertex -> E1 (axis dir)
            d2x, d2y = (x_ss - x_be), (rs - rc)      # vertex -> E2 (cone edge)
            n1 = math.hypot(d1x, d1y) or 1.0
            n2 = math.hypot(d2x, d2y) or 1.0
            bx = d1x / n1 + d2x / n2
            by = d1y / n1 + d2y / n2
            bn = math.hypot(bx, by) or 1.0
            ra_lx = x_be + r_arc * bx / bn
            ra_ly = rc   + r_arc * by / bn
            a(f"(setq MTAP:RA_VERT {_pt(x_be, rc)})")          # vertex: taper begin (body top)
            a(f"(setq MTAP:RA_E1   {_pt(x_ss, rc)})")          # ray 1: horizontal (axis dir)
            a(f"(setq MTAP:RA_E2   {_pt(x_ss, rs)})")          # ray 2: up the cone edge
            a(f"(setq MTAP:RA_LOC  {_pt(ra_lx, ra_ly)})")      # arc/label on the bisector
        else:
            a("(setq MTAP:RA_VERT nil MTAP:RA_E1 nil MTAP:RA_E2 nil MTAP:RA_LOC nil)")
        a("")

        # diameter dims
        dc_dimx = -(max(self._par, 0.0) + gap * 1.4)
        a(f"(setq MTAP:XPB    {_num(p.x_point_base)})")
        a(f"(setq MTAP:RC     {_num(rc)})")
        a(f"(setq MTAP:DCDIMX {_num(dc_dimx)})")
        a(f'(setq MTAP:DCSTR  "{p.cutting_diameter:g}")')
        has_d = abs(p.shank_diameter - p.cutting_diameter) > 1e-3
        a(f"(setq MTAP:HASD   {_bool(has_d)})")
        a(f"(setq MTAP:XEND   {_num(p.x_end)})")
        a(f"(setq MTAP:RS     {_num(rs)})")
        a(f"(setq MTAP:DDIMX  {_num(p.overall_length + gap * 1.4)})")
        a(f'(setq MTAP:DSTR   "{p.shank_diameter:g}")')
        a("")

        # length dims
        near    = -(rmax + gap * 2.0)
        far     = -(rmax + gap * 3.6)
        tip_bot = rc if p.point_length <= EPS else 0.0
        a(f"(setq MTAP:NEAR {_num(near)})")
        a(f"(setq MTAP:FAR  {_num(far)})")
        has_fl = p.x_shank_start > EPS
        a(f"(setq MTAP:HASFL {_bool(has_fl)})")
        if has_fl:
            a(f"(setq MTAP:FL1 {_pt(0.0, -tip_bot)})")
            a(f"(setq MTAP:FL2 {_pt(p.x_shank_start, -rs)})")
        else:
            a("(setq MTAP:FL1 nil MTAP:FL2 nil)")
        a(f"(setq MTAP:LS1  {_pt(p.x_shank_start, -rs)})")
        a(f"(setq MTAP:LS2  {_pt(p.x_end, -rs)})")
        a(f"(setq MTAP:OAL1 {_pt(0.0, -tip_bot)})")
        a(f"(setq MTAP:OAL2 {_pt(p.x_end, -rs)})")
        a("")

        # GD&T block — placed directly ABOVE the Dc dimension (it controls Dc).
        # Frame is CENTERED over the Dc dimension line (x = dc_dimx); a short
        # leader drops from the frame down onto the Dc dimension.
        has_gdt = p.runout > 0
        a(f"(setq MTAP:HASGDT {_bool(has_gdt)})")
        if has_gdt:
            fy      = rmax + gap * 0.8           # just above the part top
            fx      = dc_dimx                    # bottom-left corner sits at the Dc dim
            # datum sits on the shank, toward the back face (clear of chamfer)
            dat_x   = p.x_shank_start + p.shank_length * 0.70
            a(f"(setq MTAP:GDTINS  {_pt(fx, fy)})")
            a(f'(setq MTAP:GDTVAL  "{p.runout:.3f}")')
            # leader anchored at the frame's BOTTOM-LEFT corner, dropping straight down
            a(f"(setq MTAP:GDT_LDR1 {_pt(fx, fy)})")   # bottom-left corner of frame
            a(f"(setq MTAP:GDT_LDR2 {_pt(fx, rc)})")   # straight down to the part top
            # datum: floats above the shank with its own leader down to the surface
            dat_y = rs + gap * 1.5
            a(f"(setq MTAP:DATINS   {_pt(dat_x, dat_y)})")
            a(f"(setq MTAP:DAT_LDR1 {_pt(dat_x, dat_y)})")  # bottom-left corner of datum
            a(f"(setq MTAP:DAT_LDR2 {_pt(dat_x, rs)})")      # down to shank surface
        else:
            for v in ("MTAP:GDTINS","MTAP:GDTVAL","MTAP:GDT_LDR1","MTAP:GDT_LDR2",
                      "MTAP:DATINS","MTAP:DAT_LDR1","MTAP:DAT_LDR2"):
                a(f"(setq {v} nil)")
        a("")

        # back taper block + leader
        # Positioned in the body, biased toward the shank transition and well
        # clear of the point angle (which lives at the tip end of the body).
        has_bt = p.back_taper > 0
        a(f"(setq MTAP:HASBT {_bool(has_bt)})")
        if has_bt:
            bt_x = p.x_point_base + p.body_length * 0.60  # toward transition, clear of point
            bt_y = rmax + gap * 1.5          # sit just above the body
            a(f"(setq MTAP:BTINS   {_pt(bt_x, bt_y)})")
            a(f'(setq MTAP:BTVAL   "{p.back_taper:.3f}")')
            # leader drops straight from the block's bottom-left corner to body
            a(f"(setq MTAP:BT_LDR1 {_pt(bt_x, bt_y)})")  # bottom-left corner of block
            a(f"(setq MTAP:BT_LDR2 {_pt(bt_x, rc)})")    # straight down to body surface
        else:
            a("(setq MTAP:BTINS nil MTAP:BTVAL nil MTAP:BT_LDR1 nil MTAP:BT_LDR2 nil)")
        a("")

        # general note
        a(f"(setq MTAP:NOTEPT {_pt(p.overall_length / 2.0, far - gap * 2.2)})")
        a('(setq MTAP:NOTE "ALL DIMENSIONS IN MM")')
        a("")

        # auto-execute
        a("(MTAP:draw)")
        a("(princ)")
        a("")

        return "\n".join(lines)
