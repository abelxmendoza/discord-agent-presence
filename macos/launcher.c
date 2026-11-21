/*
 * Agent Presence Agent: the login service's launcher on macOS.
 *
 *   agent-presence-agent <program> [args...]
 *
 * launchd starts this small app instead of Python, and it starts Python as a
 * child process. macOS charges a child's privacy requests (Accessibility,
 * Automation of System Events) to the app that launched it, so the
 * permission belongs to "Agent Presence", whose identity stays the same when
 * Homebrew or python.org updates Python. Run Python directly and every Python
 * update looks like a new program that has to be allowed again.
 *
 * It does nothing else: passes TERM/INT/HUP/QUIT on to the child, waits, and
 * exits with the child's status so launchd's KeepAlive behaves as before.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static volatile sig_atomic_t child = 0;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    static const int sigs[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT};
    const size_t nsigs = sizeof sigs / sizeof sigs[0];

    if (argc < 2) {
        fprintf(stderr, "usage: %s <program> [args...]\n", argv[0]);
        return 64;
    }

    /* Hold the signals until the child's pid is known, so none is lost. */
    sigset_t block, empty;
    sigemptyset(&block);
    sigemptyset(&empty);
    for (size_t i = 0; i < nsigs; i++) sigaddset(&block, sigs[i]);
    sigprocmask(SIG_BLOCK, &block, NULL);

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    for (size_t i = 0; i < nsigs; i++) sigaction(sigs[i], &sa, NULL);

    /* The child starts with no signals blocked and default handlers. */
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setsigmask(&attr, &empty);
    posix_spawnattr_setsigdefault(&attr, &block);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);

    pid_t pid;
    int rc = posix_spawn(&pid, argv[1], NULL, &attr, argv + 1, environ);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        fprintf(stderr, "agent-presence-agent: can't start %s: %s\n", argv[1], strerror(rc));
        return 127;
    }
    child = pid;
    sigprocmask(SIG_UNBLOCK, &block, NULL);

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("agent-presence-agent: waitpid");
            return 1;
        }
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}
