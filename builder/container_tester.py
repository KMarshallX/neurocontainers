#!/usr/bin/env python3
"""
Portable Container Testing Tool for NeuroContainers

This tool provides a unified interface for testing containers across different
container runtimes (Docker, Apptainer/Singularity) and storage systems (CVMFS).

Features:
- Multi-runtime support (Docker, Apptainer, Singularity)
- CVMFS integration for distributed container access
- Test definition extraction from embedded YAML files
- Automatic test execution for release PRs
- Portable test execution environment
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Union, Any
import shutil
import urllib.request
import hashlib


class ContainerRuntime:
    """Base class for container runtime implementations"""

    def __init__(self):
        self.name = self.__class__.__name__.lower()

    def is_available(self) -> bool:
        """Check if the runtime is available on the system"""
        raise NotImplementedError

    def run_test(
        self,
        container_ref: str,
        test_script: str,
        volumes: List[Dict[str, str]] = None,
        gpu: bool = False,
        working_dir: str = "/test",
    ) -> subprocess.CompletedProcess:
        """Run a test script in the container"""
        raise NotImplementedError

    def extract_file(
        self, container_ref: str, file_path: str, output_path: str
    ) -> bool:
        """Extract a file from the container"""
        raise NotImplementedError


class DockerRuntime(ContainerRuntime):
    """Docker container runtime implementation"""

    def __init__(self):
        super().__init__()
        self.name = "docker"

    def is_available(self) -> bool:
        return shutil.which("docker") is not None

    def run_test(
        self,
        container_ref: str,
        test_script: str,
        volumes: List[Dict[str, str]] = None,
        gpu: bool = False,
        working_dir: str = "/test",
    ) -> subprocess.CompletedProcess:
        cmd = ["docker", "run", "--rm"]

        # Add volumes
        if volumes:
            for vol in volumes:
                cmd.extend(["-v", f"{vol['host']}:{vol['container']}"])

        # Add GPU support
        if gpu:
            cmd.extend(["--gpus", "all"])

        # Set working directory
        cmd.extend(["-w", working_dir])

        # Add container and command
        cmd.extend([container_ref, "bash", "-c", test_script])

        return subprocess.run(cmd, capture_output=True, text=True)

    def extract_file(
        self, container_ref: str, file_path: str, output_path: str
    ) -> bool:
        try:
            # Create a temporary container to extract the file
            result = subprocess.run(
                ["docker", "create", container_ref], capture_output=True, text=True
            )

            if result.returncode != 0:
                return False

            container_id = result.stdout.strip()

            try:
                # Copy file from container
                subprocess.run(
                    ["docker", "cp", f"{container_id}:{file_path}", output_path],
                    check=True,
                )
                return True
            finally:
                # Clean up temporary container
                subprocess.run(["docker", "rm", container_id], capture_output=True)
        except subprocess.CalledProcessError:
            return False


class ApptainerRuntime(ContainerRuntime):
    """Apptainer/Singularity container runtime implementation"""

    def __init__(self):
        super().__init__()
        self.name = "apptainer"

    def is_available(self) -> bool:
        return (
            shutil.which("apptainer") is not None
            or shutil.which("singularity") is not None
        )

    def _get_command(self) -> str:
        """Get the appropriate command (apptainer or singularity)"""
        if shutil.which("apptainer"):
            return "apptainer"
        elif shutil.which("singularity"):
            return "singularity"
        else:
            raise RuntimeError("Neither apptainer nor singularity found")

    def run_test(
        self,
        container_ref: str,
        test_script: str,
        volumes: List[Dict[str, str]] = None,
        gpu: bool = False,
        working_dir: str = "/test",
    ) -> subprocess.CompletedProcess:
        cmd = [self._get_command(), "exec"]

        # Add volumes (bind mounts)
        if volumes:
            for vol in volumes:
                cmd.extend(["-B", f"{vol['host']}:{vol['container']}"])

        # Add GPU support
        if gpu:
            cmd.append("--nv")

        # Set working directory
        cmd.extend(["--pwd", working_dir])

        # Add container and command
        cmd.extend([container_ref, "bash", "-c", test_script])

        return subprocess.run(cmd, capture_output=True, text=True)

    def extract_file(
        self, container_ref: str, file_path: str, output_path: str
    ) -> bool:
        try:
            cmd = [self._get_command(), "exec", container_ref, "cat", file_path]
            result = subprocess.run(cmd, capture_output=True)

            if result.returncode == 0:
                with open(output_path, "wb") as f:
                    f.write(result.stdout)
                return True
        except subprocess.CalledProcessError:
            pass
        return False


class CVMFSContainerLocator:
    """Locate containers in CVMFS"""

    def __init__(self, cvmfs_base: str = "/cvmfs/neurodesk.ardc.edu.au"):
        self.cvmfs_base = cvmfs_base

    def is_available(self) -> bool:
        """Check if CVMFS is mounted and accessible"""
        return os.path.exists(self.cvmfs_base) and os.path.isdir(self.cvmfs_base)

    def find_container(self, name: str, version: str) -> Optional[str]:
        """Find a container in CVMFS by name and version"""
        # Common CVMFS container paths
        potential_paths = [
            f"{self.cvmfs_base}/containers/{name}_{version}.sif",
            f"{self.cvmfs_base}/containers/{name}/{version}.sif",
            f"{self.cvmfs_base}/singularity/{name}_{version}.sif",
            f"{self.cvmfs_base}/singularity/{name}/{version}.sif",
        ]

        for path in potential_paths:
            if os.path.exists(path):
                return path

        return None

    def list_containers(self, name_filter: str = None) -> List[Dict[str, str]]:
        """List available containers in CVMFS"""
        containers = []

        container_dirs = [
            f"{self.cvmfs_base}/containers",
            f"{self.cvmfs_base}/singularity",
        ]

        for container_dir in container_dirs:
            if not os.path.exists(container_dir):
                continue

            for item in os.listdir(container_dir):
                if item.endswith(".sif"):
                    # Parse name_version.sif format
                    name_version = item[:-4]
                    if "_" in name_version:
                        name, version = name_version.rsplit("_", 1)
                        if not name_filter or name_filter in name:
                            containers.append(
                                {
                                    "name": name,
                                    "version": version,
                                    "path": os.path.join(container_dir, item),
                                }
                            )

        return containers


class TestDefinitionExtractor:
    """Extract test definitions from containers and YAML files"""

    def __init__(self, runtime: ContainerRuntime):
        self.runtime = runtime

    def extract_from_container(self, container_ref: str) -> Optional[Dict[str, Any]]:
        """Extract test definitions from embedded YAML in container"""
        # Try to extract build.yaml from container
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".yaml", delete=False
        ) as temp_file:
            if self.runtime.extract_file(container_ref, "/build.yaml", temp_file.name):
                try:
                    with open(temp_file.name, "r") as f:
                        build_config = yaml.safe_load(f)
                    os.unlink(temp_file.name)
                    return self._extract_tests_from_config(build_config)
                finally:
                    if os.path.exists(temp_file.name):
                        os.unlink(temp_file.name)

        return None

    def extract_from_file(self, config_path: str) -> Optional[Dict[str, Any]]:
        """Extract test definitions from a YAML file"""
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            return self._extract_tests_from_config(config)
        except (FileNotFoundError, yaml.YAMLError):
            return None

    def _extract_tests_from_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract test definitions from build configuration"""
        tests = []

        # Extract tests from build directives
        if "build" in config and "directives" in config["build"]:
            tests.extend(self._walk_directives(config["build"]["directives"]))

        # Look for separate test definitions
        if "tests" in config:
            tests.extend(config["tests"])

        return {
            "name": config.get("name", "unknown"),
            "version": config.get("version", "unknown"),
            "tests": tests,
        }

    def _walk_directives(
        self, directives: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Walk through build directives to find test definitions"""
        tests = []

        for directive in directives:
            if "test" in directive:
                tests.append(directive["test"])
            elif "group" in directive:
                tests.extend(self._walk_directives(directive["group"]))

        return tests


class ReleaseContainerDownloader:
    """Download containers from release PR URLs"""

    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".cache", "neurocontainers"
        )
        os.makedirs(self.cache_dir, exist_ok=True)

        # Base URLs for NeuroContainers
        self.base_urls = [
            "https://neurocontainers.neurodesk.org/temporary-builds-new",
        ]

    def download_from_release(
        self, name: str, version: str, build_date: str
    ) -> Optional[str]:
        """Download container using release build information"""
        # The primary format for release containers includes the build date
        # URL format: https://neurocontainers.neurodesk.org/temporary-builds-new/{name}_{version}_{build_date}.simg
        filenames = [
            f"{name}_{version}_{build_date}.simg",  # Primary format for releases
        ]

        for filename in filenames:
            cache_path = os.path.join(self.cache_dir, filename)

            # Check cache first
            if os.path.exists(cache_path):
                print(f"Using cached container: {cache_path}")
                return cache_path

            # Try downloading from each base URL
            for base_url in self.base_urls:
                url = f"{base_url}/{filename}"
                print(f"Attempting to download: {url}")

                try:
                    urllib.request.urlretrieve(url, cache_path)
                    print(f"Successfully downloaded: {cache_path}")
                    return cache_path
                except Exception as e:
                    print(f"Failed to download from {url}: {e}")
                    continue

        return None

    def extract_build_date_from_release(self, release_file: str) -> Optional[str]:
        """Extract build date from release JSON file"""
        try:
            with open(release_file, "r") as f:
                release_data = json.load(f)

            # Get the first app's version (build date)
            apps = release_data.get("apps", {})
            if apps:
                first_app = list(apps.values())[0]
                return first_app.get("version")
        except Exception:
            pass
        return None


class ContainerTester:
    """Main container testing orchestrator"""

    def __init__(self):
        self.runtimes = [DockerRuntime(), ApptainerRuntime()]
        self.cvmfs = CVMFSContainerLocator()
        self.release_downloader = ReleaseContainerDownloader()
        self.test_extractor = None
        self.selected_runtime = None

    def select_runtime(self, preferred: str = None) -> ContainerRuntime:
        """Select the best available container runtime"""
        if preferred:
            for runtime in self.runtimes:
                # Handle both "apptainer" and "singularity" for ApptainerRuntime
                runtime_names = [runtime.name]
                if runtime.name == "apptainer":
                    runtime_names.append("singularity")
                
                if preferred.lower() in runtime_names and runtime.is_available():
                    self.selected_runtime = runtime
                    self.test_extractor = TestDefinitionExtractor(runtime)
                    return runtime

        # Auto-select first available runtime
        for runtime in self.runtimes:
            if runtime.is_available():
                self.selected_runtime = runtime
                self.test_extractor = TestDefinitionExtractor(runtime)
                return runtime

        raise RuntimeError("No container runtime available")

    def find_container(
        self, name: str, version: str, location: str = "auto", release_file: str = None
    ) -> Optional[str]:
        """Find a container across different locations"""
        if location == "auto" or location == "cvmfs":
            if self.cvmfs.is_available():
                cvmfs_path = self.cvmfs.find_container(name, version)
                if cvmfs_path:
                    return cvmfs_path

        if location == "auto" or location == "local":
            # Check for local .sif/.simg files
            local_paths = [
                f"{name}_{version}.sif",
                f"{name}_{version}.simg",
                f"sifs/{name}_{version}.sif",
                f"sifs/{name}_{version}.simg",
                f"./{name}_{version}.sif",
                f"./{name}_{version}.simg",
            ]

            for path in local_paths:
                if os.path.exists(path):
                    return os.path.abspath(path)

        if location == "auto" or location == "release":
            # Try to download using release information
            build_date = None
            if release_file and os.path.exists(release_file):
                build_date = self.release_downloader.extract_build_date_from_release(
                    release_file
                )

            downloaded_path = self.release_downloader.download_from_release(
                name, version, build_date
            )
            if downloaded_path:
                return downloaded_path

        if location == "auto" or location == "docker":
            # For Docker, the container reference is the tag
            return f"{name}:{version}"

        return None

    def run_test_suite(
        self,
        container_ref: str,
        test_config: Dict[str, Any],
        gpu: bool = False,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Run a complete test suite on a container"""
        results = {
            "container": container_ref,
            "runtime": self.selected_runtime.name,
            "total_tests": len(test_config.get("tests", [])),
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "test_results": [],
        }

        for test in test_config.get("tests", []):
            test_result = self._run_single_test(container_ref, test, gpu, verbose)
            results["test_results"].append(test_result)

            if test_result["status"] == "passed":
                results["passed"] += 1
            elif test_result["status"] == "failed":
                results["failed"] += 1
            else:
                results["skipped"] += 1

        return results

    def _run_single_test(
        self,
        container_ref: str,
        test: Dict[str, Any],
        gpu: bool = False,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Run a single test on a container"""
        test_name = test.get("name", "Unnamed Test")

        if verbose:
            print(f"Running test: {test_name}")

        result = {
            "name": test_name,
            "status": "skipped",
            "stdout": "",
            "stderr": "",
            "return_code": -1,
        }

        # Handle manual tests
        if test.get("manual", False):
            result["status"] = "skipped"
            result["stderr"] = "Manual test - skipped in automated run"
            return result

        # Handle builtin tests
        if "builtin" in test:
            return self._run_builtin_test(container_ref, test, gpu, verbose)

        # Handle script tests
        if "script" in test:
            script = test["script"]
            if isinstance(script, list):
                script = " && ".join(script)

            # Create test volume if needed
            volumes = []
            if "prep" in test:
                # TODO: Implement prep step handling
                pass

            try:
                proc_result = self.selected_runtime.run_test(
                    container_ref, script, volumes, gpu
                )

                result["stdout"] = proc_result.stdout
                result["stderr"] = proc_result.stderr
                result["return_code"] = proc_result.returncode
                result["status"] = "passed" if proc_result.returncode == 0 else "failed"

            except Exception as e:
                result["stderr"] = str(e)
                result["status"] = "failed"

        return result

    def _run_builtin_test(
        self,
        container_ref: str,
        test: Dict[str, Any],
        gpu: bool = False,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Run a builtin test (like test_deploy.sh)"""
        builtin_name = test["builtin"]

        # Find builtin test script
        script_path = os.path.join(os.path.dirname(__file__), builtin_name)
        if not os.path.exists(script_path):
            return {
                "name": test.get("name", builtin_name),
                "status": "failed",
                "stdout": "",
                "stderr": f"Builtin test {builtin_name} not found",
                "return_code": -1,
            }

        # Read the builtin test script
        with open(script_path, "r") as f:
            script_content = f.read()

        try:
            proc_result = self.selected_runtime.run_test(
                container_ref, script_content, [], gpu
            )

            return {
                "name": test.get("name", builtin_name),
                "status": "passed" if proc_result.returncode == 0 else "failed",
                "stdout": proc_result.stdout,
                "stderr": proc_result.stderr,
                "return_code": proc_result.returncode,
            }
        except Exception as e:
            return {
                "name": test.get("name", builtin_name),
                "status": "failed",
                "stdout": "",
                "stderr": str(e),
                "return_code": -1,
            }


def main():
    parser = argparse.ArgumentParser(
        description="Portable Container Testing Tool for NeuroContainers"
    )

    parser.add_argument(
        "container", nargs="?", help="Container name:version or path to container file"
    )
    parser.add_argument(
        "--runtime",
        choices=["docker", "apptainer", "singularity"],
        help="Preferred container runtime",
    )
    parser.add_argument(
        "--location",
        choices=["auto", "cvmfs", "docker", "local"],
        default="auto",
        help="Where to find the container",
    )
    parser.add_argument("--test-config", help="Path to test configuration file (YAML)")
    parser.add_argument(
        "--gpu", action="store_true", help="Enable GPU support for tests"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output", "-o", help="Output file for test results (JSON)")
    parser.add_argument(
        "--list-containers",
        action="store_true",
        help="List available containers in CVMFS",
    )
    parser.add_argument(
        "--release-file", help="Path to release JSON file for build date extraction"
    )

    args = parser.parse_args()

    tester = ContainerTester()

    # List containers if requested
    if args.list_containers:
        if tester.cvmfs.is_available():
            containers = tester.cvmfs.list_containers()
            for container in containers:
                print(
                    f"{container['name']}:{container['version']} -> {container['path']}"
                )
        else:
            print("CVMFS not available")
        return

    # Select runtime
    try:
        runtime = tester.select_runtime(args.runtime)
        if args.verbose:
            print(f"Using container runtime: {runtime.name}")
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse container reference if provided
    if args.container:
        if ":" in args.container and not args.container.endswith(".sif"):
            name, version = args.container.split(":", 1)
        else:
            # Assume it's a path or name without version
            name = args.container
            version = "latest"
    else:
        if not args.list_containers:
            print(
                "Error: Container argument required unless using --list-containers",
                file=sys.stderr,
            )
            sys.exit(1)
        name = version = None

    # Find container (skip if just listing)
    if not args.list_containers:
        container_ref = tester.find_container(
            name, version, args.location, args.release_file
        )
        if not container_ref:
            print(f"Error: Container {name}:{version} not found", file=sys.stderr)
            sys.exit(1)

    if not args.list_containers:
        if args.verbose:
            print(f"Found container: {container_ref}")

        # Load test configuration
        test_config = None
        if args.test_config:
            test_config = tester.test_extractor.extract_from_file(args.test_config)
        else:
            # Try to extract from container
            test_config = tester.test_extractor.extract_from_container(container_ref)

        if not test_config or not test_config.get("tests"):
            print("Error: No test configuration found", file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            print(f"Found {len(test_config['tests'])} tests")

        # Run tests
        results = tester.run_test_suite(
            container_ref, test_config, args.gpu, args.verbose
        )

    if not args.list_containers:
        # Output results
        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)

        # Print summary
        print(f"\nTest Results for {container_ref}:")
        print(f"  Total: {results['total_tests']}")
        print(f"  Passed: {results['passed']}")
        print(f"  Failed: {results['failed']}")
        print(f"  Skipped: {results['skipped']}")

        if args.verbose:
            print("\nDetailed Results:")
            for test_result in results["test_results"]:
                status_icon = (
                    "✓"
                    if test_result["status"] == "passed"
                    else "✗" if test_result["status"] == "failed" else "⊝"
                )
                print(f"  {status_icon} {test_result['name']}: {test_result['status']}")
                if test_result["status"] == "failed" and test_result["stderr"]:
                    print(f"    Error: {test_result['stderr']}")

        # Exit with error code if any tests failed
        sys.exit(1 if results["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
