package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

const testVersion = "2.3.4"
const testArchive = "CommunityStore-2.3.4.zip"

func makeArchive(t *testing.T, extra string) []byte {
	t.Helper()
	var data bytes.Buffer
	writer := zip.NewWriter(&data)
	files := map[string]string{
		"CommunityStore/CommunityStore.py": "def run(context): pass",
		"CommunityStore/CommunityStore.manifest": fmt.Sprintf(
			`{"id":%q,"type":"addin","autodeskProduct":"Fusion360","version":%q,"runOnStartup":true,"supportedOS":"mac|windows|linux"}`,
			productID, testVersion),
	}
	if extra != "" {
		files[extra] = "unexpected content"
	}
	for path, content := range files {
		entry, err := writer.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func fakeRelease(t *testing.T, body []byte, checksum []byte, prerelease bool) *httptest.Server {
	t.Helper()
	hash := fmt.Sprintf("%x", sha256.Sum256(checksum))
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/latest":
			err := json.NewEncoder(response).Encode(map[string]any{
				"tag_name": "v" + testVersion, "prerelease": prerelease,
				"assets": []releaseAsset{
					{Name: testArchive, State: "uploaded", Digest: "sha256:" + hash},
					{Name: testArchive + ".sha256", State: "uploaded"},
				},
			})
			if err != nil {
				t.Error(err)
			}
		case "/download/v" + testVersion + "/" + testArchive + ".sha256":
			fmt.Fprintf(response, "%s  %s\n", hash, testArchive)
		case "/download/v" + testVersion + "/" + testArchive:
			response.Write(body)
		default:
			http.NotFound(response, request)
		}
	}))
	t.Cleanup(server.Close)
	return server
}

func TestInstallStableReleaseAndKeepExisting(t *testing.T) {
	archive := makeArchive(t, "")
	server := fakeRelease(t, archive, archive, false)
	root := t.TempDir()
	status, err := install(root, func() ([]byte, string, error) {
		return fetchRelease(server.Client(), server.URL+"/latest", server.URL+"/download")
	})
	if status != 0 || err != nil {
		t.Fatalf("status %d: %v", status, err)
	}
	entry := filepath.Join(root, "FusionAddins", product, product+".py")
	original, err := os.ReadFile(entry)
	if err != nil {
		t.Fatal(err)
	}
	_, err = install(root, func() ([]byte, string, error) {
		t.Fatal("installer contacted GitHub for an existing installation")
		return nil, "", nil
	})
	if !errors.Is(err, errExists) {
		t.Fatal(err)
	}
	data, err := os.ReadFile(entry)
	if err != nil || !bytes.Equal(original, data) {
		t.Fatalf("existing installation was changed: %v", err)
	}
}

func TestFailedInstallLeavesNoFiles(t *testing.T) {
	archive := makeArchive(t, "")
	traversal := makeArchive(t, "CommunityStore/../outside.py")
	for _, example := range []struct {
		name       string
		archive    []byte
		checksum   []byte
		prerelease bool
	}{
		{"broken checksum", archive, []byte("other content"), false},
		{"preview release", archive, archive, true},
		{"unsafe archive", traversal, traversal, false},
	} {
		t.Run(example.name, func(t *testing.T) {
			server := fakeRelease(t, example.archive, example.checksum, example.prerelease)
			root := t.TempDir()
			status, err := install(root, func() ([]byte, string, error) {
				return fetchRelease(server.Client(), server.URL+"/latest", server.URL+"/download")
			})
			if status != 20 || err == nil {
				t.Fatalf("status %d: %v", status, err)
			}
			files, err := os.ReadDir(root)
			if err != nil || len(files) != 0 {
				t.Fatalf("unexpected files after failure: %v (%v)", files, err)
			}
		})
	}
}

func TestNoStableRelease(t *testing.T) {
	server := httptest.NewServer(http.NotFoundHandler())
	defer server.Close()
	_, _, err := fetchRelease(server.Client(), server.URL+"/latest", server.URL+"/download")
	if !errors.Is(err, errNoRelease) {
		t.Fatal(err)
	}
}

func TestRateLimitedReleaseUsesVerifiedWebAssets(t *testing.T) {
	archive := makeArchive(t, "")
	for _, apiStatus := range []int{http.StatusForbidden, http.StatusTooManyRequests} {
		for _, mismatch := range []bool{false, true} {
			t.Run(fmt.Sprintf("%d/mismatch=%v", apiStatus, mismatch), func(t *testing.T) {
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					switch r.URL.Path {
					case "/api":
						w.WriteHeader(apiStatus)
					case "/releases/latest":
						http.Redirect(w, r, "/releases/tag/v"+testVersion, http.StatusFound)
					case "/releases/download/v" + testVersion + "/" + testArchive + ".sha256":
						payload := archive
						if mismatch {
							payload = []byte("different release")
						}
						fmt.Fprintf(w, "%x  %s\n", sha256.Sum256(payload), testArchive)
					case "/releases/download/v" + testVersion + "/" + testArchive:
						w.Write(archive)
					default:
						t.Errorf("unexpected request for %s", r.URL.Path)
						http.NotFound(w, r)
					}
				}))
				defer server.Close()
				got, version, err := fetchRelease(server.Client(), server.URL+"/api", server.URL+"/releases/download")
				if mismatch {
					if err == nil || got != nil {
						t.Fatal("accepted a mismatched ZIP")
					}
				} else if err != nil || version != testVersion || !bytes.Equal(got, archive) {
					t.Fatalf("version %s: %v", version, err)
				}
			})
		}
	}
}

func TestWebTagOnlyAcceptsExactReleaseLocation(t *testing.T) {
	for _, target := range []string{
		"https://example.invalid/releases/tag/v1.0.0", "/other/tag/v1.0.0",
		"/releases/tag/v1.0.0-rc1", "/releases/tag/v1.0.0?download=1",
		"/releases/tag/v1.0.0/other", "/releases/tag/v1.0.0#fragment",
	} {
		t.Run(target, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				http.Redirect(w, r, target, http.StatusFound)
			}))
			defer server.Close()
			if tag, err := latestWebTag(server.Client(), server.URL+"/releases"); err == nil {
				t.Fatalf("unexpected tag %s", tag)
			}
		})
	}
}
