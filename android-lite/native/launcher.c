/* `python` for NewAl Code Lite: python.org's Android build is a library (libpython3.14.so) for apps to embed; this
 * is the program around it, shipped as libnewalpy.so so Android unpacks it where apps may run programs. It runs
 * NewAl Code, and the agent's own `python3` commands (a link to it). */
#include <Python.h>

int main(int argc, char **argv) {
    return Py_BytesMain(argc, argv);
}
