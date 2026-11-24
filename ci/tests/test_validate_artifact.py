"""Tests for ci/validate_artifact.py and ci/elfinfo.py.

Run from the repository root with:
    python3 -m unittest discover -s ci/tests -v
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CI_DIR = os.path.dirname(HERE)
sys.path.insert(0, CI_DIR)
sys.path.insert(0, HERE)

import elfinfo  # noqa: E402
import fixtures  # noqa: E402
import validate_artifact as va  # noqa: E402

CONTRACT_PATH = os.path.join(CI_DIR, "artifact-contract.json")
TAG = "b12669"
VARIANT = "10.2.0"
STATIC_CHECKS = ["metadata", "archive-integrity", "safe-extraction", "inventory",
                 "permissions", "elf-architecture", "dependency-closure", "required-linkage"]


def statuses(report) -> dict:
    return {c["id"]: c["status"] for c in report["checks"]}


def problems(report, check_id) -> str:
    check = next(c for c in report["checks"] if c["id"] == check_id)
    return "\n".join(check["details"].get("problems", [])) + "\n" + check["summary"]


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="validate-artifact-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.contract = va.load_contract(CONTRACT_PATH)

    def archive_path(self, tag=TAG, variant=VARIANT, directory=None):
        directory = directory or self.tmp
        return os.path.join(directory, va.expected_archive_name(self.contract, tag, variant))

    def build(self, entries=None, tag=TAG, variant=VARIANT, **kwargs):
        entries = fixtures.good_entries() if entries is None else entries
        return fixtures.write_archive(self.archive_path(tag, variant), entries, **kwargs)

    def validate(self, path, tag=TAG, variant=VARIANT, contract=None, contract_path=CONTRACT_PATH,
                 static_only=True, **kwargs):
        return va.validate_archive(contract or self.contract, contract_path, path, tag, variant,
                                   static_only=static_only, **kwargs)


class ElfReaderTests(unittest.TestCase):
    def test_reads_dynamic_information(self):
        data = fixtures.build_elf(interp=fixtures.INTERP, needed=["libfoo.so.1", "libc.so.6"],
                                  soname="libbar.so.2", rpath="$ORIGIN::/build",
                                  verneed={"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.38"]})
        info = elfinfo.parse_elf_bytes(data)
        self.assertEqual(info.elf_class, "ELF64")
        self.assertEqual(info.byte_order, "little")
        self.assertEqual(info.machine, "x86_64")
        self.assertEqual(info.interpreter, fixtures.INTERP)
        self.assertEqual(info.needed, ["libfoo.so.1", "libc.so.6"])
        self.assertEqual(info.soname, "libbar.so.2")
        self.assertEqual(info.rpath, ["$ORIGIN", "", "/build"])
        self.assertIsNone(info.runpath)
        self.assertEqual(info.version_requirements, {"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.38"]})
        self.assertTrue(info.is_dynamic_executable)

    def test_shared_library_is_not_an_executable(self):
        info = elfinfo.parse_elf_bytes(fixtures.build_elf(soname="libx.so.1"))
        self.assertFalse(info.is_dynamic_executable)

    def test_rejects_malformed_input(self):
        for data in (b"", b"\x7fELF", b"MZ" + bytes(100),
                     b"\x7fELF" + bytes([9, 1]) + bytes(58),
                     fixtures.build_elf(needed=["libc.so.6"])[:80]):
            with self.subTest(data=data[:8]):
                with self.assertRaises(elfinfo.ElfError):
                    elfinfo.parse_elf_bytes(data)


class ContractTests(TempDirTestCase):
    def test_release_gate_requires_only_known_checks(self):
        report = self.validate(self.build())
        produced = {c["id"] for c in report["checks"]}
        self.assertLessEqual(set(self.contract["release_gate"]["required_checks"]), produced)
        self.assertIn("loader-resolution", self.contract["release_gate"]["required_checks"])

    def test_every_variant_has_a_distinct_archive_name(self):
        names = {va.expected_archive_name(self.contract, TAG, v)
                 for v in self.contract["rocm_variants"]}
        self.assertEqual(len(names), len(self.contract["rocm_variants"]))
        self.assertIn("llama-cpp-b12669-rocm-10.2.0-gfx1151.tar.xz", names)

    def test_rejects_malformed_contract(self):
        bad = os.path.join(self.tmp, "contract.json")
        for content in ("not json", json.dumps({"schema_version": 99}),
                        json.dumps({"schema_version": 1})):
            with open(bad, "w") as handle:
                handle.write(content)
            with self.assertRaises(va.ContractError):
                va.load_contract(bad)


class ValidArchiveTests(TempDirTestCase):
    def test_good_archive_passes_static_checks(self):
        path = self.build()
        report = self.validate(path)
        result = statuses(report)
        for check_id in STATIC_CHECKS:
            self.assertEqual(result[check_id], va.PASS, problems(report, check_id))
        self.assertEqual(result["rpath-portability"], va.PASS)
        self.assertEqual(result["loader-resolution"], va.NOT_RUN)
        self.assertEqual(result["startup"], va.NOT_RUN)
        # Static success alone is not release-eligible: loader resolution is required.
        self.assertEqual(report["result"], va.RESULT_INCOMPLETE)

    def test_report_identifies_artifact_and_versions(self):
        path = self.build()
        report = self.validate(path, rocm_version_detected="10.2.12345")
        self.assertEqual(report["schema_version"], va.REPORT_SCHEMA_VERSION)
        self.assertEqual(report["artifact"]["filename"], os.path.basename(path))
        self.assertEqual(report["artifact"]["sha256"], va.sha256_file(path))
        self.assertEqual(report["upstream_tag"], TAG)
        self.assertEqual(report["rocm_variant"], VARIANT)
        self.assertEqual(report["rocm_version_detected"], "10.2.12345")
        self.assertEqual(report["contract"]["sha256"], va.sha256_file(CONTRACT_PATH))
        self.assertEqual(report["abi_requirements"]["GLIBC"], "2.38")
        self.assertEqual(report["abi_requirements"]["GLIBCXX"], "3.4.32")
        self.assertIn("llama-server", report["inventory"]["executables"])
        self.assertEqual(report["inventory"]["symlinks"]["libllama.so.1"], "libllama.so.1.0.0")
        closure = next(c for c in report["checks"] if c["id"] == "dependency-closure")
        self.assertEqual(closure["details"]["external"]["libamdhip64.so.7"]["provided_by"],
                         "ROCm runtime")
        json.dumps(report)  # must be serializable

    def test_v_tags_are_accepted(self):
        report = self.validate(self.build(tag="v0.4.1"), tag="v0.4.1")
        self.assertEqual(statuses(report)["metadata"], va.PASS)

    def test_archive_without_dot_prefix_is_accepted(self):
        report = self.validate(self.build(prefix=""))
        self.assertEqual(statuses(report)["safe-extraction"], va.PASS)
        self.assertEqual(statuses(report)["inventory"], va.PASS)

    def test_build_host_rpath_is_advisory(self):
        entries = fixtures.good_entries()
        entries["llama-cli"] = fixtures.executable(
            ["libllama.so.1", "libc.so.6"], rpath="/home/runner/_work/x/build/bin::/opt/rocm/lib")
        report = self.validate(self.build(entries))
        check = next(c for c in report["checks"] if c["id"] == "rpath-portability")
        self.assertEqual(check["status"], va.WARN)
        self.assertFalse(check["mandatory"])
        self.assertIn("DT_RPATH '/home/runner/_work/x/build/bin'",
                      check["details"]["non_portable_entries"])
        self.assertIn("DT_RPATH ''", check["details"]["non_portable_entries"])
        self.assertNotIn("DT_RPATH '/opt/rocm/lib'", check["details"]["non_portable_entries"])
        self.assertEqual(report["result"], va.RESULT_INCOMPLETE)


class FailureTestCase(TempDirTestCase):
    def assert_fails(self, report, check_id, expected_text):
        self.assertEqual(statuses(report)[check_id], va.FAIL, problems(report, check_id))
        self.assertIn(expected_text, problems(report, check_id))
        self.assertEqual(report["result"], va.RESULT_FAIL)


class MalformedArchiveTests(FailureTestCase):

    def test_truncated_archive(self):
        path = self.build()
        with open(path, "rb") as handle:
            data = handle.read()
        with open(path, "wb") as handle:
            handle.write(data[: len(data) // 2])
        report = self.validate(path)
        self.assert_fails(report, "archive-integrity", "not a readable .tar.xz")
        self.assertEqual(statuses(report)["inventory"], va.NOT_RUN)

    def test_corrupted_bytes(self):
        path = self.build()
        with open(path, "r+b") as handle:
            handle.seek(os.path.getsize(path) // 2)
            byte = handle.read(1)
            handle.seek(-1, os.SEEK_CUR)
            handle.write(bytes([byte[0] ^ 0xFF]))
        self.assert_fails(self.validate(path), "archive-integrity", "not a readable .tar.xz")

    def test_wrong_compression(self):
        path = self.archive_path()
        with tarfile.open(path, "w:gz") as tar:
            tar.add(__file__, arcname="llama-cli")
        self.assert_fails(self.validate(path), "archive-integrity", "not a readable .tar.xz")

    def test_missing_archive_file(self):
        self.assert_fails(self.validate(self.archive_path()), "archive-integrity",
                          "not a readable .tar.xz")

    def test_archive_without_files(self):
        self.assert_fails(self.validate(self.build({})), "archive-integrity", "no files")

    def test_path_traversal(self):
        entries = fixtures.good_entries()
        entries["../evil"] = {"data": b"x"}
        self.assert_fails(self.validate(self.build(entries, prefix="")), "safe-extraction",
                          "parent directory traversal")

    def test_absolute_path(self):
        info = tarfile.TarInfo("/etc/evil")
        info.size = 1
        path = self.build(extra_members=[(info, b"x")])
        self.assert_fails(self.validate(path), "safe-extraction", "absolute path")

    def test_symlink_escaping_archive(self):
        entries = fixtures.good_entries()
        entries["libescape.so"] = fixtures.symlink("../../etc/passwd")
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction",
                          "outside the archive")

    def test_absolute_symlink(self):
        entries = fixtures.good_entries()
        entries["libabs.so"] = fixtures.symlink("/usr/lib/libc.so.6")
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction",
                          "absolute symlink target")

    def test_dangling_symlink(self):
        entries = fixtures.good_entries()
        del entries["libggml.so.1.0.0"]
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction", "is missing")

    def test_hardlink(self):
        entries = fixtures.good_entries()
        entries["llama-alias"] = {"hardlink": "llama-cli"}
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction",
                          "hardlink entries are not allowed")

    def test_device_node(self):
        info = tarfile.TarInfo("./null")
        info.type = tarfile.CHRTYPE
        path = self.build(extra_members=[(info, None)])
        self.assert_fails(self.validate(path), "safe-extraction", "special entries")

    def test_setuid_executable(self):
        entries = fixtures.good_entries()
        entries["llama-cli"]["mode"] = 0o4755
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction", "setuid")

    def test_world_writable_file(self):
        entries = fixtures.good_entries()
        entries["libggml.so.1.0.0"]["mode"] = 0o777
        self.assert_fails(self.validate(self.build(entries)), "safe-extraction", "writable")

    def test_duplicate_member(self):
        info = tarfile.TarInfo("./llama-cli")
        info.size = 1
        info.mode = 0o755
        path = self.build(extra_members=[(info, b"x")])
        self.assert_fails(self.validate(path), "safe-extraction", "duplicate member")


class IncompletePackageTests(FailureTestCase):
    def test_missing_required_executable(self):
        entries = fixtures.good_entries()
        del entries["llama-server"]
        self.assert_fails(self.validate(self.build(entries)), "inventory",
                          "required executable llama-server is missing")

    def test_required_executable_is_not_a_program(self):
        entries = fixtures.good_entries()
        entries["llama-bench"] = {"data": b"#!/bin/sh\necho hi\n", "mode": 0o755}
        self.assert_fails(self.validate(self.build(entries)), "inventory",
                          "required executable llama-bench is missing or not an ELF file")

    def test_required_executable_without_interpreter(self):
        entries = fixtures.good_entries()
        entries["llama-bench"] = fixtures.library("libbench.so")
        self.assert_fails(self.validate(self.build(entries)), "inventory",
                          "llama-bench is not a dynamic executable")

    def test_missing_hip_backend(self):
        entries = fixtures.good_entries()
        for name in ("libggml-hip.so.1.0.0", "libggml-hip.so.1", "libggml-hip.so"):
            del entries[name]
        report = self.validate(self.build(entries))
        self.assert_fails(report, "inventory", "required library libggml-hip")
        self.assertEqual(statuses(report)["dependency-closure"], va.FAIL)
        self.assertEqual(statuses(report)["required-linkage"], va.FAIL)

    def test_missing_bundled_library(self):
        entries = fixtures.good_entries()
        for name in ("libllama.so.1.0.0", "libllama.so.1", "libllama.so"):
            del entries[name]
        report = self.validate(self.build(entries))
        self.assert_fails(report, "dependency-closure",
                          "needs libllama.so.1, which is neither bundled")
        self.assertIn("libllama", problems(report, "inventory"))

    def test_missing_soname_link(self):
        entries = fixtures.good_entries()
        del entries["libllama.so"]
        del entries["libllama.so.1"]
        self.assert_fails(self.validate(self.build(entries)), "dependency-closure",
                          "llama-cli: needs libllama.so.1")

    def test_unexpected_external_dependency(self):
        entries = fixtures.good_entries()
        entries["llama-cli"] = fixtures.executable(["libllama.so.1", "libcurl.so.4"])
        self.assert_fails(self.validate(self.build(entries)), "dependency-closure",
                          "needs libcurl.so.4")

    def test_soname_mismatch(self):
        entries = fixtures.good_entries()
        entries["libggml.so.1.0.0"] = fixtures.library("libggml.so.2", ["libc.so.6"])
        self.assert_fails(self.validate(self.build(entries)), "dependency-closure",
                          "has SONAME libggml.so.2")

    def test_hip_backend_without_hip_runtime(self):
        entries = fixtures.good_entries()
        entries["libggml-hip.so.1.0.0"] = fixtures.library("libggml-hip.so.1",
                                                          ["libggml-base.so.1", "libc.so.6"])
        self.assert_fails(self.validate(self.build(entries)), "required-linkage",
                          "does not need ^libamdhip64")

    def test_executable_without_execute_permission(self):
        entries = fixtures.good_entries()
        entries["llama-bench"]["mode"] = 0o644
        self.assert_fails(self.validate(self.build(entries)), "permissions",
                          "executable llama-bench has mode 0o644")

    def test_wrong_architecture(self):
        entries = fixtures.good_entries()
        entries["libggml-cpu.so.1.0.0"] = fixtures.library(
            "libggml-cpu.so.1", ["libc.so.6"], machine=fixtures.EM_AARCH64)
        self.assert_fails(self.validate(self.build(entries)), "elf-architecture",
                          "libggml-cpu.so.1.0.0: machine aarch64 != x86_64")

    def test_wrong_interpreter(self):
        entries = fixtures.good_entries()
        entries["llama-cli"] = fixtures.executable(["libc.so.6"], interp="/lib/ld-musl-x86_64.so.1")
        self.assert_fails(self.validate(self.build(entries)), "elf-architecture",
                          "interpreter /lib/ld-musl-x86_64.so.1")

    def test_truncated_elf(self):
        entries = fixtures.good_entries()
        entries["libggml-base.so.1.0.0"]["data"] = entries["libggml-base.so.1.0.0"]["data"][:70]
        self.assert_fails(self.validate(self.build(entries)), "elf-architecture",
                          "libggml-base.so.1.0.0: unparsable ELF")


class MetadataTests(FailureTestCase):
    def test_filename_does_not_match_tag(self):
        path = self.build(tag="b12000")
        self.assert_fails(self.validate(path, tag=TAG), "metadata", "!= expected")

    def test_unknown_rocm_variant(self):
        path = self.build(variant="6.4.0")
        self.assert_fails(self.validate(path, variant="6.4.0"), "metadata",
                          "ROCm variant '6.4.0' is not one of")

    def test_invalid_upstream_tag(self):
        path = self.build(tag="main")
        self.assert_fails(self.validate(path, tag="main"), "metadata",
                          "upstream tag 'main' does not match")

    def test_renamed_archive(self):
        path = self.build()
        renamed = os.path.join(self.tmp, "llama-cpp.tar.xz")
        os.rename(path, renamed)
        self.assert_fails(self.validate(renamed), "metadata", "archive filename")


class ResultTests(TempDirTestCase):
    def test_overall_result(self):
        required = self.contract["release_gate"]["required_checks"]
        passing = [va.Check(cid, True, va.PASS, "") for cid in required]
        self.assertEqual(va.overall_result(self.contract, passing), va.RESULT_PASS)
        advisory = passing + [va.Check("rpath-portability", False, va.WARN, ""),
                              va.Check("startup", True, va.NOT_RUN, "")]
        self.assertEqual(va.overall_result(self.contract, advisory), va.RESULT_PASS)
        not_run = passing[:-1] + [va.Check(required[-1], True, va.NOT_RUN, "")]
        self.assertEqual(va.overall_result(self.contract, not_run), va.RESULT_INCOMPLETE)
        failed = passing + [va.Check("startup", True, va.FAIL, "")]
        self.assertEqual(va.overall_result(self.contract, failed), va.RESULT_FAIL)

    def test_startup_not_run_on_incompatible_cpu(self):
        archive = va.Archive(members={}, root=self.tmp)
        environment = {"system": "Linux", "machine": "x86_64", "cpu_flags": ["avx2", "fma"]}
        check = va.check_startup(self.contract, archive, None, environment, None)
        self.assertEqual(check.status, va.NOT_RUN)
        self.assertIn("avx512f", check.details["missing_cpu_flags"])
        self.assertIn("lacks instruction set extensions", check.summary)

    def test_runtime_unavailable_reasons(self):
        linux = {"system": "Linux", "machine": "x86_64"}
        self.assertIn("--static-only",
                      va.runtime_unavailable_reason(self.contract, linux, True))
        self.assertIn("not Linux", va.runtime_unavailable_reason(
            self.contract, {"system": "Darwin", "machine": "arm64"}, False))
        self.assertIn("not x86_64", va.runtime_unavailable_reason(
            self.contract, {"system": "Linux", "machine": "aarch64"}, False))

    def test_parse_loader_listing(self):
        output = textwrap.dedent("""\
            \tlinux-vdso.so.1 (0x00007ffd5a9f2000)
            \tlibllama.so.1 => /tmp/x/libllama.so.1 (0x00007f0e1c000000)
            \tlibamdhip64.so.7 => not found
            \t/lib64/ld-linux-x86-64.so.2 (0x00007f0e1c400000)
            """)
        self.assertEqual(va.parse_loader_listing(output),
                         {"libllama.so.1": "/tmp/x/libllama.so.1", "libamdhip64.so.7": None})


class CommandLineTests(TempDirTestCase):
    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return va.main(list(args))

    def test_exit_codes(self):
        path = self.build()
        report = os.path.join(self.tmp, "reports", "report.json")
        markdown = os.path.join(self.tmp, "summary.md")
        common = ["validate", "--contract", CONTRACT_PATH, "--archive", path,
                  "--upstream-tag", TAG, "--report", report, "--markdown", markdown]
        self.assertEqual(self.run_cli(*common, "--rocm-variant", VARIANT, "--static-only"), 3)
        with open(report) as handle:
            self.assertEqual(json.load(handle)["result"], va.RESULT_INCOMPLETE)
        with open(markdown) as handle:
            self.assertIn("| `loader-resolution` | yes | ⏭️ not_run |", handle.read())
        self.assertEqual(self.run_cli(*common, "--rocm-variant", "6.4.0", "--static-only"), 1)
        bad_contract = os.path.join(self.tmp, "bad.json")
        with open(bad_contract, "w") as handle:
            handle.write("{}")
        self.assertEqual(self.run_cli("gate", "--contract", bad_contract, "--upstream-tag", TAG,
                                      "--archives-dir", self.tmp, "--reports-dir", self.tmp), 2)


class ReleaseGateTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.archives = os.path.join(self.tmp, "release")
        self.reports = os.path.join(self.tmp, "validation")
        os.makedirs(self.archives)
        os.makedirs(self.reports)

    def passing_report(self, path, variant):
        """A static report upgraded as if loader resolution passed on a matching host."""
        report = self.validate(path, variant=variant)
        for check in report["checks"]:
            if check["id"] == "loader-resolution":
                check["status"] = va.PASS
        report["result"] = va.overall_result(
            self.contract, [va.Check(c["id"], c["mandatory"], c["status"], "")
                            for c in report["checks"]])
        self.assertEqual(report["result"], va.RESULT_PASS)
        return report

    def publish_all(self):
        reports = {}
        for variant in self.contract["rocm_variants"]:
            path = fixtures.write_archive(self.archive_path(TAG, variant, self.archives),
                                          fixtures.good_entries())
            reports[variant] = self.passing_report(path, variant)
            self.write_report(reports[variant])
        return reports

    def write_report(self, report, name=None):
        name = name or report["artifact"]["filename"] + ".validation.json"
        va.write_json(os.path.join(self.reports, name), report)

    def gate(self, tag=TAG, contract_path=CONTRACT_PATH):
        return va.gate_release(self.contract, contract_path, tag, self.archives, self.reports)

    def assert_blocked(self, summary, expected_text):
        self.assertEqual(summary["result"], va.RESULT_FAIL)
        self.assertIn(expected_text, "\n".join(summary["problems"]))

    def test_all_variants_validated(self):
        self.publish_all()
        summary = self.gate()
        self.assertEqual(summary["result"], va.RESULT_PASS, summary["problems"])
        self.assertEqual([a["rocm_variant"] for a in summary["artifacts"]],
                         self.contract["rocm_variants"])
        for artifact in summary["artifacts"]:
            self.assertEqual(artifact["sha256"],
                             va.sha256_file(os.path.join(self.archives, artifact["filename"])))
        self.assertIn("release gate", va.markdown_gate(summary).lower())

    def test_missing_variant(self):
        self.publish_all()
        os.remove(self.archive_path(TAG, "7.14.1", self.archives))
        self.assert_blocked(self.gate(), "rocm-7.14.1-gfx1151.tar.xz: archive is missing")

    def test_unexpected_file_would_be_published(self):
        self.publish_all()
        with open(os.path.join(self.archives, "notes.txt"), "w") as handle:
            handle.write("x")
        self.assert_blocked(self.gate(), "notes.txt: unexpected file")

    def test_missing_report(self):
        self.publish_all()
        os.remove(os.path.join(self.reports, os.listdir(self.reports)[0]))
        self.assert_blocked(self.gate(), "no validation report")

    def test_archive_changed_after_validation(self):
        self.publish_all()
        path = self.archive_path(TAG, "10.1.0", self.archives)
        entries = fixtures.good_entries()
        del entries["llama-server"]
        fixtures.write_archive(path, entries)
        self.assert_blocked(self.gate(), "report sha256 does not match the archive bytes")

    def test_failed_report(self):
        reports = self.publish_all()
        report = copy.deepcopy(reports["10.2.0"])
        report["result"] = va.RESULT_FAIL
        for check in report["checks"]:
            if check["id"] == "inventory":
                check["status"] = va.FAIL
        self.write_report(report)
        summary = self.gate()
        self.assert_blocked(summary, "report result is 'fail'")
        self.assert_blocked(summary, "required check inventory is fail")

    def test_required_check_not_run(self):
        reports = self.publish_all()
        report = copy.deepcopy(reports["10.1.0"])
        for check in report["checks"]:
            if check["id"] == "loader-resolution":
                check["status"] = va.NOT_RUN
        self.write_report(report)  # result field still claims "pass"
        self.assert_blocked(self.gate(), "required check loader-resolution is not_run")

    def test_report_for_other_tag(self):
        self.publish_all()
        for variant in self.contract["rocm_variants"]:
            os.rename(self.archive_path(TAG, variant, self.archives),
                      self.archive_path("b99999", variant, self.archives))
        self.assert_blocked(self.gate(tag="b99999"), "no validation report")

    def test_report_metadata_mismatch(self):
        reports = self.publish_all()
        report = copy.deepcopy(reports["7.14.1"])
        report["rocm_variant"] = "10.2.0"
        report["upstream_tag"] = "b1"
        self.write_report(report)
        summary = self.gate()
        self.assert_blocked(summary, "report rocm_variant '10.2.0' != '7.14.1'")
        self.assert_blocked(summary, "report upstream_tag 'b1' != 'b12669'")

    def test_report_from_different_contract(self):
        self.publish_all()
        changed = os.path.join(self.tmp, "contract.json")
        with open(CONTRACT_PATH) as src, open(changed, "w") as dst:
            dst.write(src.read() + "\n")
        self.assert_blocked(self.gate(contract_path=changed), "different contract")

    def test_duplicate_reports(self):
        reports = self.publish_all()
        self.write_report(reports["10.2.0"], name="copy.json")
        self.assert_blocked(self.gate(), "duplicate report")

    def test_unreadable_report(self):
        self.publish_all()
        with open(os.path.join(self.reports, "broken.json"), "w") as handle:
            handle.write("{")
        self.assert_blocked(self.gate(), "broken.json: unreadable report")


def can_compile_native_elf() -> bool:
    return (platform.system() == "Linux" and platform.machine() == "x86_64"
            and shutil.which("gcc") is not None
            and os.path.exists(fixtures.INTERP))


@unittest.skipUnless(can_compile_native_elf(), "needs Linux x86_64 with gcc")
class LoaderAndStartupTests(TempDirTestCase):
    """End-to-end runtime checks with real binaries and the real dynamic loader."""

    def setUp(self):
        super().setUp()
        self.contract = copy.deepcopy(self.contract)
        self.contract["required_executables"] = ["llama-cli"]
        self.contract["required_libraries"] = ["libllama"]
        self.contract["required_linkage"] = []
        self.contract["runtime"]["startup_cpu_flags"] = []
        self.contract["runtime"]["startup_checks"] = [
            {"executable": "llama-cli", "args": ["--version"]}]
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def compile(self, *args):
        subprocess.run(["gcc", *args], cwd=self.src, check=True)

    def build_native(self, *, version_exit=0, extra_lib=None, rpath=None):
        with open(os.path.join(self.src, "llama.c"), "w") as handle:
            handle.write("int llama_answer(void) { return 42; }\n")
        with open(os.path.join(self.src, "cli.c"), "w") as handle:
            handle.write(textwrap.dedent(f"""\
                #include <stdio.h>
                int llama_answer(void);
                int main(void) {{
                    printf("version: %d\\n", llama_answer());
                    return {version_exit};
                }}
                """))
        self.compile("-shared", "-fPIC", "-Wl,-soname,libllama.so.1", "-o",
                     "libllama.so.1.0.0", "llama.c")
        os.symlink("libllama.so.1.0.0", os.path.join(self.src, "libllama.so.1"))
        link = ["-o", "llama-cli", "cli.c", "-L.", "-l:libllama.so.1"]
        if extra_lib:
            with open(os.path.join(self.src, "extra.c"), "w") as handle:
                handle.write("int extra(void) { return 0; }\n")
            self.compile("-shared", "-fPIC", f"-Wl,-soname,{extra_lib}", "-o", extra_lib,
                         "extra.c")
            link += ["-Wl,--no-as-needed", f"-l:{extra_lib}"]
        if rpath:
            link += [f"-Wl,--disable-new-dtags,-rpath,{rpath}"]
        self.compile(*link)
        entries = {}
        for name in ("llama-cli", "libllama.so.1.0.0"):
            with open(os.path.join(self.src, name), "rb") as handle:
                entries[name] = {"data": handle.read(), "mode": 0o755}
        entries["libllama.so.1"] = fixtures.symlink("libllama.so.1.0.0")
        return self.build(entries)

    def test_native_archive_passes_all_checks(self):
        report = self.validate(self.build_native(), contract=self.contract, static_only=False)
        result = statuses(report)
        self.assertEqual(result["loader-resolution"], va.PASS,
                         problems(report, "loader-resolution"))
        self.assertEqual(result["startup"], va.PASS, problems(report, "startup"))
        self.assertEqual(report["result"], va.RESULT_PASS)
        startup = next(c for c in report["checks"] if c["id"] == "startup")
        self.assertIn("version: 42", startup["details"]["results"][0]["stdout"])

    def test_unresolvable_external_dependency(self):
        # Allowed by the contract (ROCm runtime) but absent on this host.
        path = self.build_native(extra_lib="libamdhip64.so.7")
        report = self.validate(path, contract=self.contract, static_only=False)
        self.assertEqual(statuses(report)["dependency-closure"], va.PASS)
        self.assertEqual(statuses(report)["loader-resolution"], va.FAIL)
        self.assertIn("libamdhip64.so.7 => not found", problems(report, "loader-resolution"))
        self.assertEqual(statuses(report)["startup"], va.NOT_RUN)
        self.assertEqual(report["result"], va.RESULT_FAIL)

    def test_bundled_library_resolved_from_build_tree(self):
        # DT_RPATH beats LD_LIBRARY_PATH: a surviving build tree must not mask the archive.
        path = self.build_native(rpath=self.src)
        report = self.validate(path, contract=self.contract, static_only=False)
        self.assertEqual(statuses(report)["loader-resolution"], va.FAIL)
        self.assertIn("resolved outside the archive", problems(report, "loader-resolution"))

    def test_startup_failure(self):
        report = self.validate(self.build_native(version_exit=3), contract=self.contract,
                               static_only=False)
        self.assertEqual(statuses(report)["loader-resolution"], va.PASS)
        self.assertEqual(statuses(report)["startup"], va.FAIL)
        self.assertIn("llama-cli --version exited 3", problems(report, "startup"))
        self.assertEqual(report["result"], va.RESULT_FAIL)


if __name__ == "__main__":
    unittest.main()
