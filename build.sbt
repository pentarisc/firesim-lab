// =============================================================================
//  firesim-lab/build.sbt
// =============================================================================

val firesimRoot = file(
  sys.env.getOrElse("FIRESIM_ROOT", "../firesim")
) / "sim"

// ── FireSim ProjectRef bindings ───────────────────────────────────────────────
lazy val firesimLib  = ProjectRef(firesimRoot, "firesim")
lazy val midas       = ProjectRef(firesimRoot, "midas")
lazy val targetutils = ProjectRef(firesimRoot, "targetutils")

// ── Settings shared by ALL sub-projects ───────────────────────────────────────
lazy val commonSettings = Seq(
  organization  := "firesim-lab",
  scalaVersion  := "2.13.10",           // must match firesim exactly
  scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked"),
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
  )

// =============================================================================
//  TARGET: my-baremetal
//  Opts into: uart, fased (blockdev not needed here)
// =============================================================================
lazy val myBaremetal = (project in file("targets/my-baremetal"))
  .dependsOn(common, firesimLib, midas, targetutils)  // ← depends on common
  .settings(commonSettings)
  .settings(
    name := "my-baremetal",
  )

// =============================================================================
//  TARGET: my-second-target (example: uart + blockdev)
// =============================================================================
// lazy val mySecondTarget = (project in file("targets/my-second-target"))
//   .dependsOn(common, firesimLib, midas, targetutils)
//   .settings(commonSettings)
//   .settings(
//     name := "my-second-target",
//   )

// =============================================================================
//  Root aggregate
// =============================================================================
lazy val root = (project in file("."))
  .aggregate(common, myBaremetal)    // add mySecondTarget here when ready
  .settings(
    name           := "firesim-lab",
    publish / skip := true,
  )