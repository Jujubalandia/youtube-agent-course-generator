// src/services/api.ts
import axios, { AxiosResponse } from "axios";
// Keep ParsedBackendResponse if needed elsewhere, but the initial response changes
import { ParsedBackendResponse } from "../pages/UploadPage";

const API_BASE_URL = "http://localhost:8000/api";

// Define the response type for the initial request
interface StartGenerationResponse {
    message: string;
    video_id?: string; // Optional if it returns existing course directly
    database_id?: number; // Optional if it returns existing course directly
    course?: ParsedBackendResponse['course']; // Optional if it returns existing course directly
}

// Renamed function to better reflect its action
export const startCourseGeneration = async (videoUrl: string): Promise<AxiosResponse<StartGenerationResponse>> => {
    // This endpoint now returns quickly with video_id or existing data
    const response = await axios.post<StartGenerationResponse>(`${API_BASE_URL}/generate-course`, { videoUrl });
    return response;
};

// We don't export uploadTranscript anymore as its purpose changed

export {}; // Keep if you have other exports, otherwise remove