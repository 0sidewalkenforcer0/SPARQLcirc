package npcs;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** UTF-8 text I/O shared by the command-line entry points. */
public final class Utf8Text {

    private Utf8Text() {}

    /** Read UTF-8 text and discard only a leading byte-order mark. */
    public static String read(Path path) throws IOException {
        return stripBom(new String(Files.readAllBytes(path), StandardCharsets.UTF_8));
    }

    static String stripBom(String text) {
        return text.startsWith("\uFEFF") ? text.substring(1) : text;
    }

    /** Write one line independently of the platform default charset. */
    public static void println(String text) throws IOException {
        System.out.write((text + System.lineSeparator()).getBytes(StandardCharsets.UTF_8));
        System.out.flush();
    }
}
