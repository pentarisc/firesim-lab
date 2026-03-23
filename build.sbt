// =============================================================================
//  firesim-lab/build.sbt
// =============================================================================

val firesimRoot = file(
  sys.env.getOrElse("FIRESIM_ROOT", "../firesim")
) / "sim"

val chiselVersion = "3.6.1"

// ── FireSim ProjectRef bindings ───────────────────────────────────────────────
lazy val firesimLib  = ProjectRef(firesimRoot, "firesim")
lazy val midas       = ProjectRef(firesimRoot, "midas")
lazy val targetutils = ProjectRef(firesimRoot, "targetutils")

// ── Settings shared by ALL sub-projects ───────────────────────────────────────
lazy val commonSettings = Seq(
  organization  := "firesim-lab",
  scalaVersion  := "2.13.10",           // must match firesim exactly
  scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked", "-warn-unused"),
  addCompilerPlugin("edu.berkeley.cs" % "chisel3-plugin" % chiselVersion cross CrossVersion.full),
  libraryDependencies += "edu.berkeley.cs" %% "chisel3" % chiselVersion,
  Compile / unmanagedResourceDirectories +=
    (Compile / baseDirectory).value / "src" / "main" / "resources",
)

// =============================================================================
//  COMMON — shared bridge library
//  Contains: bridge Scala stubs, GoldenGate Scala (compiled separately via
//  makefrag symlink hook), and C++ drivers (referenced by target driver.mk).
//
//  This project is a pure library — it has no Generator, no top, no config.
//  Individual targets .dependsOn(common) to get access to bridge Scala.
// =============================================================================
lazy val common = (project in file("targets/common"))
  .dependsOn(firesimLib, midas, targetutils)
  .settings(commonSettings)
  .settings(
    name := "common",
    Compile / unmanagedSourceDirectories ++= Seq(
      baseDirectory.value / "bridgeinterfaces" / "src" / "main" / "scala",
      baseDirectory.value / "bridgestubs" / "src" / "main" / "scala",
    ),
  )

// =============================================================================
//  Root aggregate
// =============================================================================
lazy val root = (project in file("."))
  .aggregate(common)
  .settings(
    name           := "firesim-lab",
    publish / skip := true,
  )