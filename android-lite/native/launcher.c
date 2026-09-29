/* `python` for NewAl Code Lite: python.org's Android build is a library (libpython3.14.so) for apps to embed; this
 * is the program around it, shipped as libnewalpy.so so Android unpacks it where apps may run programs. It runs
 * NewAl Code, and the agent's own `python3` commands (a link to it). Called through a link named `git`, it is
 * NewAl Code's git (python -m newal_code.minigit: git's commands, run by dulwich), since the phone has none. */
#include <Python.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    const char *name = strrchr(argv[0], '/');
    name = name ? name + 1 : argv[0];
    if (strcmp(name, "git") == 0) {
        char **args = malloc(sizeof(char *) * (argc + 3));
        if (!args) {
            return 1;
        }
        args[0] = argv[0];
        args[1] = "-m";
        args[2] = "newal_code.minigit";
        for (int i = 1; i < argc; i++) {
            args[i + 2] = argv[i];
        }
        args[argc + 2] = NULL;
        return Py_BytesMain(argc + 2, args);
    }
    return Py_BytesMain(argc, argv);
}
