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
