"""
test_3d.py — Standalone OpenGL viewer test.

Run with:  python test_3d.py

Shows a spinning coloured cube.  If you see the cube -> hardware is fine.
If you see a black window -> OpenGL 3.3 is broken on this machine.
No MTAP dependencies needed (only PyQt6).
"""

import sys, math, struct, ctypes

from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtOpenGL import (QOpenGLShaderProgram, QOpenGLShader,
                             QOpenGLBuffer, QOpenGLVertexArrayObject)
from PyQt6.QtGui import QMatrix4x4, QSurfaceFormat
from PyQt6.QtCore import Qt, QTimer

if sys.platform == "win32":
    _gl = ctypes.CDLL("opengl32")
else:
    try:    _gl = ctypes.CDLL("libGL.so.1")
    except: _gl = ctypes.CDLL("libGL.so")

def _glClear(m):          _gl.glClear(m)
def _glClearColor(r,g,b,a): _gl.glClearColor(ctypes.c_float(r),ctypes.c_float(g),ctypes.c_float(b),ctypes.c_float(a))
def _glEnable(c):         _gl.glEnable(c)
def _glViewport(x,y,w,h): _gl.glViewport(x,y,w,h)
def _glDrawElements(m,c,t,o): _gl.glDrawElements(m,c,t,ctypes.c_void_p(o))

_GL_DEPTH_TEST       = 0x0B71
_GL_COLOR_BUFFER_BIT = 0x00004000
_GL_DEPTH_BUFFER_BIT = 0x00000100
_GL_FLOAT            = 0x1406
_GL_UNSIGNED_INT     = 0x1405
_GL_TRIANGLES        = 0x0004

_VERT = """\
#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aCol;
uniform mat4 uMVP;
out vec3 vCol;
void main(){ vCol=aCol; gl_Position=uMVP*vec4(aPos,1.0); }
"""
_FRAG = """\
#version 330 core
in vec3 vCol;
out vec4 fragColor;
void main(){ fragColor=vec4(vCol,1.0); }
"""

# Unit cube: 8 verts [pos(3) col(3)], 12 triangles
_VERTS = struct.pack("72f",
    -1,-1,-1, 1,0,0,   1,-1,-1, 0,1,0,   1,1,-1, 0,0,1,   -1,1,-1, 1,1,0,
    -1,-1, 1, 1,0,1,   1,-1, 1, 0,1,1,   1,1, 1, 1,1,1,   -1,1, 1, 0,0,0,
)
_IDX = struct.pack("36I",
    0,1,2, 2,3,0,  4,5,6, 6,7,4,  0,1,5, 5,4,0,
    2,3,7, 7,6,2,  0,3,7, 7,4,0,  1,2,6, 6,5,1,
)

class _Cube(QOpenGLWidget):
    def __init__(self):
        fmt = QSurfaceFormat()
        fmt.setVersion(3,3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        QSurfaceFormat.setDefaultFormat(fmt)
        super().__init__()
        self.setMinimumSize(480, 360)
        self._angle = 0.0
        self._ok = False
        t = QTimer(self); t.timeout.connect(self._tick); t.start(16)

    def _tick(self):
        self._angle = (self._angle + 1.0) % 360.0
        self.update()

    def initializeGL(self):
        _glEnable(_GL_DEPTH_TEST)
        _glClearColor(0.1, 0.1, 0.1, 1.0)

        self._vao = QOpenGLVertexArrayObject(self)
        if not self._vao.create():
            print("VAO creation FAILED"); return

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create(); self._vbo.bind()
        self._vbo.allocate(_VERTS, len(_VERTS))
        self._vbo.release()

        self._ebo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self._ebo.create(); self._ebo.bind()
        self._ebo.allocate(_IDX, len(_IDX))
        self._ebo.release()

        self._prog = QOpenGLShaderProgram(self)
        ok  = self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderType.Vertex,   _VERT)
        ok &= self._prog.addShaderFromSourceCode(QOpenGLShader.ShaderType.Fragment, _FRAG)
        ok &= self._prog.link()
        if not ok:
            print("Shader link FAILED:", self._prog.log()); return
        self._ok = True
        print("initializeGL OK — OpenGL 3.3 CoreProfile working")

    def resizeGL(self, w, h):
        _glViewport(0, 0, w, h)

    def paintGL(self):
        _glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
        if not self._ok: return

        proj = QMatrix4x4(); proj.perspective(45.0, self.width()/max(self.height(),1), 0.1, 100.0)
        view = QMatrix4x4(); view.lookAt(*(3,3,3), *(0,0,0), *(0,1,0))
        model = QMatrix4x4(); model.rotate(self._angle, 0.5, 1.0, 0.3)

        self._prog.bind()
        self._vao.bind()
        self._vbo.bind()
        self._ebo.bind()

        stride = 6*4
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, _GL_FLOAT, 0,   3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, _GL_FLOAT, 3*4, 3, stride)

        self._prog.setUniformValue("uMVP", proj * view * model)
        _glDrawElements(_GL_TRIANGLES, 36, _GL_UNSIGNED_INT, 0)

        self._prog.disableAttributeArray(0)
        self._prog.disableAttributeArray(1)
        self._ebo.release()
        self._vbo.release()
        self._vao.release()
        self._prog.release()


app = QApplication(sys.argv)
win = QMainWindow()
win.setWindowTitle("MTAP 3D Test — spinning cube = hardware OK")
w = QWidget(); lay = QVBoxLayout(w)
lbl = QLabel("If you see a spinning coloured cube below → hardware is fine.\n"
             "If this area is black → OpenGL 3.3 is not working on this machine.")
lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
lay.addWidget(lbl)
cube = _Cube()
lay.addWidget(cube)
win.setCentralWidget(w)
win.resize(500, 440)
win.show()
sys.exit(app.exec())
