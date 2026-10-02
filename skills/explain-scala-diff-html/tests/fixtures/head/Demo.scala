package demo

import cats.effect.{IO, Resource}
import fs2.Stream
import scala.concurrent.duration._

sealed trait Mode
object Mode {
  case object Strict extends Mode
  case object Lenient extends Mode
}

final case class TrackId(value: String) extends AnyVal
object TrackId {
  def from(raw: String): Either[String, TrackId] =
    if (raw.matches("[a-z][a-z0-9-]*")) Right(TrackId(raw)) else Left("InvalidTrackId")
}

object Demo {
  def decode(input: String, retries: Int): Either[String, String] =
    if (retries < 0) Left("NegativeRetries")
    else if (input.isEmpty) Left("EmptyInput")
    else try Right(new String(java.util.Base64.getDecoder.decode(input), "UTF-8"))
    catch { case _: IllegalArgumentException => Left("InvalidBase64") }

  def choose(mode: Mode, retries: Int): String = mode match {
    case Mode.Strict => if (retries > 0) "retry" else "stop"
    case Mode.Lenient => if (retries >= 0) "retry" else "stop"
  }

  def lookup(id: TrackId): String = "track:" + id.value
  def ioDecode(input: String): IO[Either[String, String]] = IO(decode(input, 1))
  def resourceDecode(input: String): Resource[IO, String] = Resource.pure[IO, String]("resource:" + input)
  def streamDecode(input: String): Stream[IO, String] = Stream.emit(input).covary[IO].repeat
  def slow(): IO[String] = IO.sleep(10.seconds).as("done")
}
