/*
 * Measures sequential request/echo round trips over IPv4 loopback TCP.
 *
 * Uses two connected sockets and one echo thread in a single process.
 * Setup, payload preparation, and payload verification are not timed.
 *
 * Each blocking send/receive has a two-second inactivity timeout.
 * The external runner remains responsible for a whole-command timeout.
 *
 * Usage:
 *   tcp_roundtrip PAYLOAD_BYTES WARMUP ITERATIONS
 */

#include <cerrno>
#include <chrono>
#include <exception>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* operation) {
    throw std::system_error(
        errno,
        std::generic_category(),
        operation);
}

// Each socket has one owner and closes automatically.
class Socket {
public:
    explicit Socket(int descriptor)
        : fd_(descriptor) {
        if (fd_ < 0) {
            fail("creating socket");
        }
    }

    ~Socket() {
        ::close(fd_);
    }

    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;

    int get() const {
        return fd_;
    }

private:
    int fd_;
};

std::size_t number(
    const char* text,
    std::size_t minimum,
    std::size_t maximum) {
    const std::string input(text);

    if (
        input.empty() ||
        input.find_first_not_of("0123456789") != std::string::npos
    ) {
        throw std::invalid_argument(
            "arguments must contain only digits");
    }

    const auto value = std::stoull(input);

    if (value < minimum || value > maximum) {
        throw std::invalid_argument(
            "argument outside supported range");
    }

    return static_cast<std::size_t>(value);
}

void configure(int fd) {
    const int enabled = 1;
    const timeval timeout{2, 0};

    if (
        ::setsockopt(
            fd,
            IPPROTO_TCP,
            TCP_NODELAY,
            &enabled,
            sizeof(enabled)) != 0 ||
        ::setsockopt(
            fd,
            SOL_SOCKET,
            SO_RCVTIMEO,
            &timeout,
            sizeof(timeout)) != 0 ||
        ::setsockopt(
            fd,
            SOL_SOCKET,
            SO_SNDTIMEO,
            &timeout,
            sizeof(timeout)) != 0
    ) {
        fail("configuring socket");
    }
}

// TCP is a byte stream. One call may transfer only part of a payload.
void transfer(
    int fd,
    std::vector<unsigned char>& buffer,
    bool sending) {
    std::size_t offset = 0;

    while (offset < buffer.size()) {
        auto* position = buffer.data() + offset;
        const auto remaining = buffer.size() - offset;

        const auto count = sending
            ? ::send(fd, position, remaining, MSG_NOSIGNAL)
            : ::recv(fd, position, remaining, 0);

        if (count > 0) {
            offset += static_cast<std::size_t>(count);
        } else if (count == 0) {
            throw std::runtime_error(
                "peer closed during transfer");
        } else if (errno != EINTR) {
            fail(
                sending
                    ? "sending payload"
                    : "receiving payload");
        }
    }
}

void benchmark(
    std::size_t bytes,
    std::size_t warmup,
    std::size_t iterations) {
    Socket listener(
        ::socket(
            AF_INET,
            SOCK_STREAM | SOCK_CLOEXEC,
            0));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;

    // Port zero asks Linux to allocate an available local port.
    if (
        ::bind(
            listener.get(),
            reinterpret_cast<sockaddr*>(&address),
            sizeof(address)) != 0 ||
        ::listen(listener.get(), 1) != 0
    ) {
        fail("binding or listening");
    }

    socklen_t address_size = sizeof(address);

    if (
        ::getsockname(
            listener.get(),
            reinterpret_cast<sockaddr*>(&address),
            &address_size) != 0
    ) {
        fail("reading listener address");
    }

    Socket client(
        ::socket(
            AF_INET,
            SOCK_STREAM | SOCK_CLOEXEC,
            0));

    if (
        ::connect(
            client.get(),
            reinterpret_cast<sockaddr*>(&address),
            sizeof(address)) != 0
    ) {
        fail("connecting loopback socket");
    }

    Socket server(
        ::accept(
            listener.get(),
            nullptr,
            nullptr));

    configure(client.get());
    configure(server.get());

    std::vector<unsigned char> request(bytes);
    std::vector<unsigned char> response(bytes);
    std::vector<unsigned char> echo_buffer(bytes);

    std::vector<double> samples;
    samples.reserve(iterations);

    const auto total = warmup + iterations;

    std::exception_ptr server_error;
    std::exception_ptr client_error;

    // The echo thread receives and returns each complete payload.
    std::thread echo([&] {
        try {
            for (std::size_t index = 0; index < total; ++index) {
                transfer(server.get(), echo_buffer, false);
                transfer(server.get(), echo_buffer, true);
            }
        } catch (...) {
            server_error = std::current_exception();

            // Wake a client that may be waiting for a response.
            ::shutdown(server.get(), SHUT_RDWR);
        }
    });

    try {
        for (std::size_t index = 0; index < total; ++index) {
            // Change the pattern between exchanges.
            // Payload preparation is outside the timed interval.
            for (std::size_t offset = 0; offset < bytes; ++offset) {
                request[offset] = static_cast<unsigned char>(
                    (index + offset) % 251);
            }

            const auto started =
                std::chrono::steady_clock::now();

            transfer(client.get(), request, true);
            transfer(client.get(), response, false);

            const auto finished =
                std::chrono::steady_clock::now();

            // Verification is also outside the timed interval.
            if (response != request) {
                throw std::runtime_error(
                    "echo payload verification failed");
            }

            if (index >= warmup) {
                samples.push_back(
                    std::chrono::duration<double, std::micro>(
                        finished - started).count());
            }
        }
    } catch (...) {
        client_error = std::current_exception();

        // Wake the echo thread before joining it.
        ::shutdown(client.get(), SHUT_RDWR);
        ::shutdown(server.get(), SHUT_RDWR);
    }

    echo.join();

    if (client_error) {
        std::rethrow_exception(client_error);
    }

    if (server_error) {
        std::rethrow_exception(server_error);
    }

    // Emit one JSON record only after every payload check succeeds.
    std::cout
        << std::setprecision(17)
        << "{\"schema_version\":1,"
        << "\"benchmark\":\"tcp_roundtrip\","
        << "\"transport\":\"ipv4_loopback\","
        << "\"verified\":true,"
        << "\"payload_bytes\":" << bytes
        << ",\"warmup\":" << warmup
        << ",\"iterations\":" << iterations
        << ",\"samples_us\":[";

    for (std::size_t index = 0; index < samples.size(); ++index) {
        if (index != 0) {
            std::cout << ',';
        }

        std::cout << samples[index];
    }

    std::cout << "]}\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        if (argc != 4) {
            throw std::invalid_argument(
                "usage: tcp_roundtrip "
                "PAYLOAD_BYTES WARMUP ITERATIONS");
        }

        benchmark(
            number(argv[1], 1, 1048576),
            number(argv[2], 0, 1000),
            number(argv[3], 1, 10000));

        return 0;
    } catch (const std::exception& error) {
        std::cerr
            << "ERROR: "
            << error.what()
            << '\n';

        return 1;
    }
}