;;; MTAP stub — load this once via APPLOAD (or Startup Suite).
;;; Type  DMTAP  after clicking "AutoCAD Link" in the MTAP app.
(vl-load-com)
(defun c:DMTAP ( / f)
  (setq f (strcat (getenv "USERPROFILE") "\\MTAP\\mtap_link.lsp"))
  (if (findfile f)
    (load f)
    (princ "\nMTAP: click 'AutoCAD Link' in the MTAP app first, then run DMTAP."))
  (princ))
(princ "\nMTAP: type DMTAP after clicking AutoCAD Link in the app.\n")
(princ)
