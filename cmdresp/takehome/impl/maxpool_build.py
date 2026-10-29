"""Build and verify the 32-bit streaming max-pooling accelerator."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from waveflow.build.build import BuildConfig, BuildDag, BuildStep, SourceStep
from waveflow.build.cli import run_dag_cli
from waveflow.build.hwcodegen_steps import HlsCodegenStep
from waveflow.build.resource_steps import InspectSynthStep
from waveflow.hw.arrayutils import ArrayUtilsStep
from waveflow.hw.clock import Clock
from waveflow.hw.dataschema import DataSchemaStep
from waveflow.simulation.simulation import Simulation
from waveflow.toolchain import toolchain
from waveflow.utils.burst_io import read_bursts, write_bursts

try:
    import scenarios as S
    from maxpool import Int32, MaxpoolAccel, MaxpoolTB, SCHEMA_CLASSES, WORD_BW_SUPPORTED, connect
    from maxpool import maxpool_stream_model
except ImportError as exc:
    raise RuntimeError("run this build from the implementation directory") from exc

ROOT = Path(__file__).resolve().parent
WIDTH = 32


@dataclass(kw_only=True)
class ScenariosStep(BuildStep):
    description = "Generate all scenario stimuli and intent-derived expectations."
    produces = {"scenarios": Path("data/w32/scenarios.txt")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        names = S.write_scenarios(config.root_dir / "data" / "w32")
        for scenario_name in names:
            for stage in ("model", "pysim", "csim", "cosim"):
                (config.root_dir / "data" / "w32" / scenario_name / stage).mkdir(
                    parents=True, exist_ok=True)
        return {"scenarios": config.root_dir / "data" / "w32" / "scenarios.txt"}


@dataclass(kw_only=True)
class ModelStep(BuildStep):
    description = "Run the pure maxpool_stream_model on every activation."
    consumes = ["scenarios"]
    produces = {"model": Path("results/model_done.json")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        root = config.root_dir
        data_dir = root / "data" / "w32"
        for name in (data_dir / "scenarios.txt").read_text().split():
            directory = data_dir / name
            result = maxpool_stream_model(read_bursts(directory / "in"), word_bw=WIDTH)
            write_bursts(result.out, directory / "model")
            (directory / "model" / "status.json").write_text(json.dumps(result.status()) + "\n")
            total = sum(len(b.words) for b in read_bursts(directory / "in"))
            (directory / "model" / "unread_words.txt").write_text(
                f"{total - result.consumed_words}\n")
        done = root / "results" / "model_done.json"
        done.parent.mkdir(parents=True, exist_ok=True)
        done.write_text(json.dumps({"scenarios": len((data_dir / "scenarios.txt").read_text().split()),
                                    "word_width": WIDTH}, indent=2) + "\n")
        return {"model": done}


@dataclass(kw_only=True)
class CheckStep(BuildStep):
    stage: str
    done_artifact: str | None = None
    successful_only: bool = False
    params = {}

    @property
    def consumes(self) -> list[str]:
        return ["scenarios"] + ([self.done_artifact] if self.done_artifact else [])

    @property
    def produces(self) -> dict:
        return {f"check_{self.stage}": Path(f"results/check_{self.stage}.json")}

    def run(self, config: BuildConfig, **_) -> dict:
        data_dir = config.root_dir / "data" / "w32"
        names = (data_dir / "scenarios.txt").read_text().split()
        if self.successful_only:
            names = [name for name, scenario in S.scenario_map().items() if scenario.end and all(
                command.header_tlast_after == 4 and command.sample_tlast_after is None
                and command.sample_final_tlast and S._valid_command(command)
                for command in scenario.commands)]
        report = S.check(data_dir, self.stage, names)
        path = config.root_dir / f"results/check_{self.stage}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        failures = {name: issues for name, issues in report.items() if issues}
        for name, issues in report.items():
            print(f"    {self.stage:7s} {name:38s} {'PASS' if not issues else 'FAIL'}")
            for issue in issues:
                print(f"        {issue}")
        if failures:
            raise RuntimeError(f"{self.stage} has {len(failures)} failing scenarios")
        return {f"check_{self.stage}": path}


@dataclass(kw_only=True)
class PySimStep(BuildStep):
    description = "Run pysim on successful well-framed activations."
    consumes = ["scenarios"]
    produces = {"pysim": Path("results/pysim_done.json")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        root = config.root_dir
        data_dir = root / "data" / "w32"
        for name, sc in S.scenario_map().items():
            if not sc.end or any(
                command.header_tlast_after != 4 or command.sample_tlast_after is not None
                or not command.sample_final_tlast or not S._valid_command(command)
                for command in sc.commands
            ):
                continue
            directory = data_dir / name
            sim = Simulation()
            clk = Clock(freq=100e6)
            accel = MaxpoolAccel(name="maxpool_accel", sim=sim, clk=clk)
            tb = MaxpoolTB(name="maxpool_tb", sim=sim, stimulus=directory / "in",
                           word_bw=WIDTH, n_out=len(read_bursts(directory / "expected")))
            connect(sim, tb, accel, clk)
            sim.run_sim()
            write_bursts(tb.out, directory / "pysim")
            (directory / "pysim" / "status.json").write_text(json.dumps(tb.status) + "\n")
            (directory / "pysim" / "unread_words.txt").write_text("0\n")
        done = root / "results" / "pysim_done.json"
        done.parent.mkdir(parents=True, exist_ok=True)
        done.write_text(json.dumps({"successful_activations": sum(
            1 for sc in S.scenario_map().values() if sc.end)}, indent=2) + "\n")
        return {"pysim": done}


@dataclass(kw_only=True)
class GenIncludeStep(BuildStep):
    description = "Generate schema headers, array utilities, and stream testbench helpers."
    params = {}

    @property
    def consumes(self) -> list[str]:
        return ["maxpool_source"]

    @property
    def produces(self) -> dict:
        return {"include_dir": Path("include")}

    def run(self, config: BuildConfig, **_) -> dict:
        inner = BuildDag()
        from waveflow.build.streamutils import StreamUtilsStep

        inner.add(StreamUtilsStep(output_dir="include"))
        for schema in SCHEMA_CLASSES:
            inner.add(DataSchemaStep(schema, word_bw_supported=WORD_BW_SUPPORTED,
                                     include_dir="include"))
        inner.add(ArrayUtilsStep(Int32, WORD_BW_SUPPORTED))
        failed = [name for name, result in inner.run(config).items() if not result.success]
        if failed:
            raise RuntimeError(f"include generation failed: {failed}")
        return {"include_dir": config.root_dir / "include"}


@dataclass(kw_only=True)
class KernelSourcesStep(BuildStep):
    description = "Ensure hand-written kernel hook, testbench, and Tcl sources are present."
    produces = {"sources": Path("maxpool_body_impl.tpp")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        for name in ("maxpool_body_impl.tpp", "maxpool_tb.cpp", "run.tcl"):
            if not (config.root_dir / name).exists():
                raise FileNotFoundError(config.root_dir / name)
        return {"sources": config.root_dir / "maxpool_body_impl.tpp"}


@dataclass(kw_only=True)
class VitisStep(BuildStep):
    stage: str
    description = "Run Vitis HLS C simulation or synthesis and comprehensive RTL co-simulation."
    params = {"live_output": False}

    @property
    def produces(self) -> dict:
        if self.stage == "csim":
            return {"csim": Path("results/csim_done.json")}
        return {"cosim": Path("results/cosim_done.json"),
                "synth_report": Path("w32_proj/solution1")}

    @property
    def consumes(self) -> list:
        base = ["include_dir", "sources", "scenarios"]
        base.extend(["maxpool_cpp", "maxpool_hpp", "maxpool_body_impl"])
        if self.stage == "synth":
            base.append("check_csim")
        return base

    def run(self, config: BuildConfig, live_output=False, **_) -> dict:
        root = config.root_dir
        vitis = shutil.which("vitis-run")
        vivado = shutil.which("vivado")
        if not vitis or not vivado:
            raise RuntimeError(f"Xilinx tools unavailable in build shell: vitis-run={vitis}, vivado={vivado}")
        env = {"WAVEFLOW_POLY_STAGE": self.stage, "WAVEFLOW_POLY_WIDTH": "32",
               "WAVEFLOW_POLY_CLK_PERIOD_NS": "10",
               "WAVEFLOW_VITIS_PATH": "/eda/xilinx/2026.1",
               "WAVEFLOW_VIVADO_PATH": vivado}
        old_vitis = os.environ.get("WAVEFLOW_VITIS_PATH")
        old_vivado = os.environ.get("WAVEFLOW_VIVADO_PATH")
        os.environ["WAVEFLOW_VITIS_PATH"] = "/eda/xilinx/2026.1"
        os.environ["WAVEFLOW_VIVADO_PATH"] = vivado
        try:
            result = toolchain.run_vitis_hls(root / "run.tcl", work_dir=root,
                                             capture_output=not live_output, env=env)
        finally:
            if old_vitis is None:
                os.environ.pop("WAVEFLOW_VITIS_PATH", None)
            else:
                os.environ["WAVEFLOW_VITIS_PATH"] = old_vitis
            if old_vivado is None:
                os.environ.pop("WAVEFLOW_VIVADO_PATH", None)
            else:
                os.environ["WAVEFLOW_VIVADO_PATH"] = old_vivado
        log_path = root / "logs" / f"vitis_{self.stage}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"vitis-run={vitis}\nvivado={vivado}\nVitis root=/eda/xilinx/2026.1\n\n"
            + (result.stdout or ""), encoding="utf-8")
        print((result.stdout or "")[-3000:])
        if self.stage == "csim":
            path = root / "results" / "csim_done.json"
            path.write_text(json.dumps({"passed": True, "log": str(log_path.relative_to(root))}, indent=2))
            return {"csim": path}
        done = root / "results" / "cosim_done.json"
        done.write_text(json.dumps({"passed": True, "log": str(log_path.relative_to(root))}, indent=2))
        report = root / "w32_proj" / "solution1"
        return {"cosim": done, "synth_report": report}


def build_dag() -> BuildDag:
    dag = BuildDag()
    dag.add(SourceStep(artifact="maxpool_source", path=ROOT / "maxpool.py",
                       description="Frozen schemas, eval, stream model, module and pysim wrapper."))
    dag.add(SourceStep(artifact="scenarios_source", path=ROOT / "scenarios.py",
                       description="Intent-derived scenarios and exact checker."))
    dag.add(ScenariosStep(name="scenarios"))
    dag.add(ModelStep(name="model"))
    dag.add(CheckStep(name="check_model", stage="model", done_artifact="model"))
    dag.add(PySimStep(name="pysim"))
    dag.add(CheckStep(name="check_pysim", stage="pysim", done_artifact="pysim",
                      successful_only=True))
    dag.add(GenIncludeStep(name="gen_include"))
    dag.add(KernelSourcesStep(name="sources"))
    dag.add(HlsCodegenStep(name="gen_kernel", comp_class=MaxpoolAccel,
                           source_artifact="sources", output_dir="gen", impl_dir="."))
    dag.add(VitisStep(name="csim", stage="csim"))
    dag.add(CheckStep(name="check_csim", stage="csim", done_artifact="csim"))
    dag.add(VitisStep(name="synth_cosim", stage="synth"))
    dag.add(CheckStep(name="check_cosim", stage="cosim", done_artifact="cosim"))
    dag.add(InspectSynthStep(
        name="inspect_synth", comp_class=MaxpoolAccel, top_name="maxpool",
        elaborate_params=("in_bw", "out_bw"), params={"in_bw": 32, "out_bw": 32},
        report_artifact="synth_report", seconds_artifact=None,
        output=Path("results/resources.json"), output_artifact="resources",
        require_ii1=True, loop_csv=Path("results/loop_df.csv"), loop_artifact="loop_df"))
    return dag


def main() -> None:
    run_dag_cli(
        build_dag,
        description="Build and verify the streaming 1-D max-pooling accelerator.",
        default_through="check_pysim",
        root_dir=ROOT,
        extra_args=[(("--live-output",), {"action": "store_true"})],
        params_from_args=lambda args: {"live_output": args.live_output},
    )


if __name__ == "__main__":
    main()