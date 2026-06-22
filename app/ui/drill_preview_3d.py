"""
drill_preview_3d.py — Interactive 3D solid viewer for the Proposal Drawing screen.

Renders the tessellated drill solid with Phong shading via PyQt6's built-in
OpenGL support (QOpenGLWidget + QOpenGLShaderProgram + QOpenGLBuffer).

Controls:
    Left-drag  : orbit (azimuth + elevation)
    Scroll     : zoom in / out

Threading:
    load_bytes() is the only public mesh-loading API.  It expects VBO data
    already computed (bytes: interleaved [px py pz nx ny nz] × n_verts).
    Call it from the main thread after the worker finishes its numpy work.

GL function access:
    Raw GL calls use QOpenGLFunctions_2_1 (from PyQt6.QtOpenGL), which
    resolves function pointers through Qt's internal mechanism — the same
    as QOpenGLShaderProgram.setAttributeBuffer / enableAttributeArray.
    ctypes opengl32 calls caused a black-screen regression in the packaged
    exe because Qt may use ANGLE (OpenGL-ES-over-DX) while opengl32 targets
    the native ICD: two separate driver contexts, attribute state not shared.

Why CompatibilityProfile:
    CoreProfile 3.3 requires an explicit VAO; without one glVertexAttribPointer
    is silently ignored → black screen with no error.  CompatibilityProfile
    provides the implicit default VAO (id=0) so attribute setup works without
    needing QOpenGLVertexArrayObject.
"""

import math

from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (
    QOpenGLShaderProgram, QOpenGLShader, QOpenGLBuffer,
    QOpenGLFunctions_2_1,
)
from PyQt6.QtGui import QMatrix4x4, QVector3D, QSurfaceFormat
from PyQt6.QtCore import Qt, QPoint, QSize
from PyQt6.QtWidgets import QSizePolicy

from app.utils.logging_setup import get_logger

log = get_logger()

# ── version-safe shader-type enum ─────────────────────────────────────────────
try:
    _VERT_SHADER = QOpenGLShader.ShaderType.Vertex
    _FRAG_SHADER = QOpenGLShader.ShaderType.Fragment
except AttributeError:
    _VERT_SHADER = QOpenGLShader.ShaderTypeBit.Vertex    # type: ignore[attr-defined]
    _FRAG_SHADER = QOpenGLShader.ShaderTypeBit.Fragment  # type: ignore[attr-defined]

# ── GL constants ──────────────────────────────────────────────────────────────
_GL_TRIANGLES        = 0x0004
_GL_FLOAT            = 0x1406
_GL_DEPTH_TEST       = 0x0B71
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_BUFFER_BIT = 0x00000100

# ── GLSL 3.30 shaders (CompatibilityProfile — no "core" keyword) ──────────────
_VERT_SRC = """\
#version 330
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNorm;

uniform mat4 uMVP;
uniform mat4 uModel;

out vec3 vWorldPos;
out vec3 vNorm;

void main() {
    vec4 world  = uModel * vec4(aPos, 1.0);
    vWorldPos   = world.xyz;
    vNorm       = normalize(mat3(uModel) * aNorm);
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

_FRAG_SRC = """\
#version 330
in vec3 vWorldPos;
in vec3 vNorm;
out vec4 fragColor;

void main() {
    vec3 n = normalize(vNorm);
    if (!gl_FrontFacing) n = -n;

    vec3 L1 = normalize(vec3( 2.0,  3.0,  4.0));
    vec3 L2 = normalize(vec3(-1.5, -0.8,  0.3));

    float d1 = max(dot(n, L1), 0.0);
    float d2 = max(dot(n, L2), 0.0) * 0.28;

    vec3 base = vec3(0.72, 0.67, 0.56);
    vec3 col  = base * (0.07 + d1 + d2);

    vec3 V = normalize(vec3(0.0, 0.0, 200.0) - vWorldPos);
    vec3 H = normalize(L1 + V);
    col += 0.55 * pow(max(dot(n, H), 0.0), 80.0);

    fragColor = vec4(col, 1.0);
}
"""


class DrillPreview3D(QOpenGLWidget):
    """Interactive 3D drill viewer.

    Mesh is provided as pre-computed bytes via load_bytes() — all numpy work
    must be done on the worker thread before calling this.
    """

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)

        super().__init__(parent)
        self.setFormat(fmt)
        self.setMinimumSize(QSize(320, 260))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._prog: QOpenGLShaderProgram | None = None
        self._vbo:  QOpenGLBuffer | None        = None
        self._gl:   QOpenGLFunctions_2_1 | None = None

        self._n_verts:  int  = 0
        self._has_mesh: bool = False
        self._gl_ready: bool = False

        self._pending_vbo:   bytes | None = None
        self._pending_n:     int   = 0
        self._pending_cx:    float = 0.0
        self._pending_cy:    float = 0.0
        self._pending_cz:    float = 0.0
        self._pending_scale: float = 1.0

        # Camera
        self._azim:   float     = 35.0
        self._elev:   float     = 22.0
        self._dist:   float     = 1.5
        self._scale:  float     = 100.0
        self._center: QVector3D = QVector3D(0.0, 0.0, 0.0)

        self._last_mouse: QPoint | None = None
        self.setMouseTracking(False)

    # ── public API ────────────────────────────────────────────────────────────

    def load_bytes(self, vbo_bytes: bytes, n_verts: int,
                   cx: float, cy: float, cz: float, scale: float) -> None:
        self._pending_vbo   = vbo_bytes
        self._pending_n     = n_verts
        self._pending_cx    = cx
        self._pending_cy    = cy
        self._pending_cz    = cz
        self._pending_scale = scale
        self.update()

    def clear(self) -> None:
        self._has_mesh    = False
        self._pending_vbo = None
        self.update()

    # ── GL lifecycle ──────────────────────────────────────────────────────────

    def initializeGL(self) -> None:
        # QOpenGLFunctions_2_1 uses Qt's own resolved function pointers — the
        # same mechanism as QOpenGLShaderProgram — so attribute state and draw
        # calls target the identical driver backend (ANGLE or native WGL).
        self._gl = QOpenGLFunctions_2_1()
        if not self._gl.initializeOpenGLFunctions():
            log.error("DrillPreview3D: QOpenGLFunctions_2_1.initializeOpenGLFunctions() failed")
            self._gl = None
            return

        self._gl.glEnable(_GL_DEPTH_TEST)
        self._gl.glClearColor(0.086, 0.078, 0.059, 1.0)

        self._prog = QOpenGLShaderProgram(self)
        ok  = self._prog.addShaderFromSourceCode(_VERT_SHADER, _VERT_SRC)
        ok &= self._prog.addShaderFromSourceCode(_FRAG_SHADER, _FRAG_SRC)
        ok &= self._prog.link()
        if not ok:
            log.error("DrillPreview3D shader link failed: %s", self._prog.log())
            return

        self._gl_ready = True
        log.debug("DrillPreview3D.initializeGL OK")

    def resizeGL(self, w: int, h: int) -> None:
        if self._gl:
            self._gl.glViewport(0, 0, w, h)

    def paintGL(self) -> None:
        if not self._gl_ready or self._prog is None or self._gl is None:
            return

        self._gl.glClearColor(0.086, 0.078, 0.059, 1.0)
        self._gl.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)

        if self._pending_vbo is not None:
            self._upload_vbo(
                self._pending_vbo, self._pending_n,
                self._pending_cx, self._pending_cy,
                self._pending_cz, self._pending_scale,
            )
            self._pending_vbo = None

        if not self._has_mesh or self._vbo is None or self._n_verts == 0:
            return

        w = max(self.width(),  1)
        h = max(self.height(), 1)

        proj = QMatrix4x4()
        proj.perspective(40.0, w / h, self._scale * 0.005, self._scale * 80.0)

        az = math.radians(self._azim)
        el = math.radians(self._elev)
        r  = self._dist * self._scale
        eye = self._center + QVector3D(
            r * math.cos(el) * math.sin(az),
            r * math.sin(el),
            r * math.cos(el) * math.cos(az),
        )
        view = QMatrix4x4()
        view.lookAt(eye, self._center, QVector3D(0.0, 1.0, 0.0))
        model = QMatrix4x4()

        self._prog.bind()
        self._vbo.bind()

        stride = 6 * 4
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, _GL_FLOAT, 0,     3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, _GL_FLOAT, 3 * 4, 3, stride)

        self._prog.setUniformValue("uMVP",   proj * view * model)
        self._prog.setUniformValue("uModel", model)

        self._gl.glDrawArrays(_GL_TRIANGLES, 0, self._n_verts)

        self._prog.disableAttributeArray(0)
        self._prog.disableAttributeArray(1)
        self._vbo.release()
        self._prog.release()

    # ── VBO upload (called from paintGL while GL context is current) ──────────

    def _upload_vbo(self, data: bytes, n_verts: int,
                    cx: float, cy: float, cz: float, scale: float) -> None:
        if self._vbo is not None:
            self._vbo.destroy()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data, len(data))
        self._vbo.release()

        self._center   = QVector3D(cx, cy, cz)
        self._scale    = scale
        self._dist     = 1.5
        self._n_verts  = n_verts
        self._has_mesh = True
        log.debug("DrillPreview3D: %d verts uploaded, scale=%.1f", n_verts, scale)

    # ── input ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._last_mouse = ev.position().toPoint()

    def mouseMoveEvent(self, ev) -> None:
        if self._last_mouse is None:
            return
        if ev.buttons() & Qt.MouseButton.LeftButton:
            d = ev.position().toPoint() - self._last_mouse
            self._azim -= d.x() * 0.45
            self._elev  = max(-88.0, min(88.0, self._elev + d.y() * 0.45))
            self._last_mouse = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._last_mouse = None

    def wheelEvent(self, ev) -> None:
        factor = 0.88 if ev.angleDelta().y() > 0 else 1.14
        self._dist = max(0.4, min(18.0, self._dist * factor))
        self.update()
