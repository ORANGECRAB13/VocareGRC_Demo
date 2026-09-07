package com.vocare.translate.app

import org.junit.Assert.assertTrue
import java.io.File

/**
 * Loader for `native/fixtures` JSON files.
 *
 * The unit-test working directory is the Gradle module directory, but that is
 * not something to bet a test suite on: a fixture path that silently resolves
 * to nothing makes every test that reads it pass vacuously. So the loader
 * searches upwards for the directory, fails loudly when it is not there, and
 * asserts the file it found is not empty.
 */
object Fixtures {
    private val root: File by lazy {
        var dir: File? = File("").absoluteFile
        while (dir != null) {
            val candidate = File(dir, "native/fixtures")
            if (candidate.isDirectory) return@lazy candidate
            val sibling = File(dir, "fixtures")
            if (sibling.isDirectory && File(sibling, "poll_events.json").isFile) return@lazy sibling
            dir = dir.parentFile
        }
        throw IllegalStateException(
            "native/fixtures not found above ${File("").absolutePath} — fixtures must be read, not assumed",
        )
    }

    fun read(name: String): String {
        val file = File(root, name)
        assertTrue("fixture $name does not exist at ${file.absolutePath}", file.isFile)
        val text = file.readText()
        assertTrue("fixture $name is empty at ${file.absolutePath}", text.isNotBlank())
        return text
    }
}
