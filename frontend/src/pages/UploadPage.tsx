import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
    Box,
    Tabs,
    Tab,
    Accordion,
    AccordionSummary,
    AccordionDetails,
    Typography,
    Grid,
    Card,
    CardContent,
    Button,
    // Replace CircularProgress with LinearProgress for ongoing status
    LinearProgress, // Changed from CircularProgress
    Snackbar,
    Alert,
    ThemeProvider,
    createTheme
} from "@mui/material";
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
// Import the updated API function
import { startCourseGeneration } from "../services/api"; // Updated import
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Frame {
    frame_path: string;
    caption: string;
    info: string;
    timestamp: number;
}

interface MediaItem {
    frame_path: string;
    caption: string;
    info: string;
    timestamp: number;
}

interface StructuredContentSection {
    section_title: string;
    start_ts: number;
    end_ts: number;
    frames: Frame[];
    media: [];
}

interface StructuredContentModule {
    module_title: string;
    sections: StructuredContentSection[];
}

interface StructuredContent {
    modules: StructuredContentModule[];
    global_concepts: string[];
}

interface CourseContentSection {
    section_title: string;
    content: string;
    media: MediaItem[];
}

interface CourseContentModule {
    module_title: string;
    sections: CourseContentSection[];
}

interface CourseContent {
    modules: CourseContentModule[];
    global_concepts: string[];
}


interface QuestionItem {
    type: string;
    text: string;
    options?: string[];
    correct_answer: string;
    rationale?: string;
    review_timestamp?: string | number; 
    difficulty?: number;
}

interface QuizItem {
    module_title: string;
    section_title: string;
    questions: QuestionItem[];
}

interface QuizContent {
    quizzes: QuizItem[];
}

interface RetentionTip {
    type: string;
    description: string;
    executed_content: string;
}

interface RetentionSectionItem {
    section_title: string;
    retention_tips?: RetentionTip[];
    visual_summary_suggestion?: string;
}

interface RetentionModule {
    module_title: string;
    retention_tips: RetentionTip[];
    spaced_repetition_prompts: string[];
    scenario_examples: string[];
    section_retention?: RetentionSectionItem[];
}

interface RetentionPlanDetail {
    module_retention: RetentionModule[];
    overall_summary: string;
}

interface RetentionPlan {
    retention_plan: RetentionPlanDetail;
}


export interface ParsedBackendResponse {
    course: {
        structured_content: StructuredContent;
        quiz_content: QuizContent;
        retention_plan: RetentionPlan;
        course_content: CourseContent;
    };
    // These might not be needed if S3 URLs are in course_content
    // frames: Frame[];
    // transcript_file: string;
}

// Interface for the SSE progress messages
interface ProgressStatus {
    status: string; // e.g., "Initializing", "Transcript", "AI Processing", "Completed", "Failed"
    detail?: string | null; // User-friendly message
    error?: string | null; // Error message if status is "Failed"
    final_result?: ParsedBackendResponse | null; // The final data when status is "Completed"
}


// --- Keep your theme definition ---
const theme = createTheme({  // <--- Define the theme here
    palette: {
        primary: {
            main: '#333', // Dark gray for a sleek look
        },
        secondary: {
            main: '#555',
        },
        text: {
            primary: '#333', // Consistent dark gray for text
            secondary: '#555',
        },
        background: {
            default: '#fff', // White background
            paper: '#fff', // White for cards and paper elements
        },
        success: {
            main: '#2ecc71', // Keep success color
        },
        error: {
            main: '#e74c3c', // Keep error color
        },
    },
    typography: {
        fontFamily: [
            'Inter', // Modern, sans-serif font.  Very readable.
            '-apple-system',
            'BlinkMacSystemFont',
            '"Segoe UI"',
            'Roboto',
            '"Helvetica Neue"',
            'Arial',
            'sans-serif',
            '"Apple Color Emoji"',
            '"Segoe UI Emoji"',
            '"Segoe UI Symbol"',
        ].join(','),
        h3: {
            fontSize: '2.2rem',
            fontWeight: 600, // Semi-bold
        },
        h5: {
            fontSize: '1.5rem',
            fontWeight: 600,
        },
        h6: {
            fontSize: '1.2rem',
            fontWeight: 600,
        },
        subtitle1: {
            fontSize: '1rem',
            fontWeight: 500,
        },
        body2: {
            fontSize: '0.9rem',
        },
        button: {
            fontWeight: 500, // Slightly bolder buttons
        }
    },
    components: {
        MuiButton: {
            styleOverrides: {
                root: {
                    borderRadius: 8,
                },
            },
        },
        MuiAccordion: {
            styleOverrides: {
                root: {
                    '&:before': {
                        display: 'none',
                    },
                },
            },
        },
    },
});


const UploadPage: React.FC = () => {
    const [videoUrl, setVideoUrl] = useState("");
    const [response, setResponse] = useState<ParsedBackendResponse | null>(null);
    const [activeTab, setActiveTab] = useState(0);
    const [selectedAnswers, setSelectedAnswers] = useState<{ [questionKey: string]: string }>({});
    // --- State for SSE ---
    const [isProcessing, setIsProcessing] = useState(false); // Renamed loading
    const [progressMessage, setProgressMessage] = useState<string | null>(null);
    const [error, setError] = useState("");
    // --- End State for SSE ---
    const [embeddedVideoUrl, setEmbeddedVideoUrl] = useState<string | null>(null);
    // const [playerReady, setPlayerReady] = useState(false);  // Track if the player is ready
    const playerRef = useRef<HTMLIFrameElement>(null);
    // Ref to hold the EventSource instance
    const eventSourceRef = useRef<EventSource | null>(null);

    const extractVideoId = (url: string): string | null => {
        const regex = /(?:https?:\/\/(?:www\.)?)?youtu(?:\.be\/|be\.com\/(?:watch\?(?:feature=youtu\.be\&)?v=|v\/|embed\/))([a-zA-Z0-9_-]+)/;
        const match = url.match(regex);
        return match ? match[1] : null;
    };

    useEffect(() => {
        if (videoUrl) {
            const videoId = extractVideoId(videoUrl);
            if (videoId) {
                setEmbeddedVideoUrl(`https://www.youtube.com/embed/${videoId}?enablejsapi=1`);
            } else {
                 setEmbeddedVideoUrl(null);
            }
        } else {
            setEmbeddedVideoUrl(null);
        }
    }, [videoUrl]);

     const formatTimestamp = (timestamp: number): string => {
        if (typeof timestamp !== 'number' || isNaN(timestamp)) return "00:00:00"; // Handle invalid input
        const hours = Math.floor(timestamp / 3600).toString().padStart(2, "0");
        const minutes = Math.floor((timestamp % 3600) / 60).toString().padStart(2, "0");
        const seconds = Math.floor(timestamp % 60).toString().padStart(2, "0");
        return `${hours}:${minutes}:${seconds}`;
    };

    // Function to close the EventSource connection
    const closeEventSource = useCallback(() => {
        if (eventSourceRef.current) {
            console.log("Closing EventSource connection.");
            eventSourceRef.current.close();
            eventSourceRef.current = null;
        }
    }, []); // No dependencies needed

    // Cleanup on component unmount
    useEffect(() => {
        return () => {
            closeEventSource();
        };
    }, [closeEventSource]); // Add closeEventSource as dependency


    const listenForProgress = useCallback((videoId: string) => {
        // Ensure no existing connection
        closeEventSource();

        const url = `${process.env.REACT_APP_API_BASE_URL || "http://localhost:8000"}/api/progress/${videoId}`;
        console.log(`Connecting to SSE: ${url}`);
        const es = new EventSource(url);
        eventSourceRef.current = es;

        es.onopen = () => {
            console.log("SSE connection opened.");
            // Initial message while waiting for first update
            setProgressMessage("Connected, waiting for progress...");
        };

        es.onmessage = (event) => {
            try {
                // console.log("SSE message received:", event.data);
                const progressData: ProgressStatus = JSON.parse(event.data);

                // Update progress message
                setProgressMessage(`${progressData.status}: ${progressData.detail || ''}`);

                // Handle final states
                if (progressData.status === "Completed") {
                    console.log("Processing completed via SSE.");
                    if (progressData.final_result) {
                        setResponse(progressData.final_result);
                    } else {
                        setError("Completed but received no final data.");
                    }
                    setIsProcessing(false);
                    setProgressMessage("Course generated successfully!"); // Final user message
                    closeEventSource(); // Close connection on completion
                } else if (progressData.status === "Failed") {
                    console.error("Processing failed via SSE:", progressData.error);
                    setError(`Processing failed: ${progressData.error || 'Unknown error'}`);
                    setIsProcessing(false);
                    setProgressMessage(null); // Clear progress message on failure
                    closeEventSource(); // Close connection on failure
                }
            } catch (parseError) {
                console.error("Failed to parse SSE message:", event.data, parseError);
                setError("Received invalid progress update.");
                setIsProcessing(false); // Stop processing indicator on parse error
                setProgressMessage(null);
                closeEventSource();
            }
        };

        es.onerror = (error) => {
            console.error("SSE error:", error);
            // Don't close immediately on error, browser might retry
            // Only set error if the state isn't 'closed' which happens normally
            if (es.readyState !== EventSource.CLOSED) {
                setError("Connection error during progress updates.");
            }
            // Consider closing explicitly if readyState is CONNECTING for too long
            // For simplicity, we let the browser handle retries for now.
            // If it fails permanently, the user might need to retry the whole process.
            // We could add logic here to stop processing if errors persist.
            setIsProcessing(false); // Assume failure if SSE errors out badly
            setProgressMessage(null);
            closeEventSource(); // Close definitively on error
        };

    }, [closeEventSource]); // Add dependency


    const handleSubmit = async () => {
        // Reset state before starting
        setIsProcessing(true);
        setError("");
        setResponse(null);
        setProgressMessage("Initiating course generation..."); // Initial message
        setActiveTab(0); // Reset to first tab
        setSelectedAnswers({}); // Clear previous quiz answers

        try {
            // Call the API to START the generation
            const result = await startCourseGeneration(videoUrl);

            // Check if the backend returned existing data directly
             if (result.data.message === "Course already exists." && result.data.course) {
                console.log("Course already exists, displaying data.");
                setResponse({ course: result.data.course }); // Set the response directly
                setIsProcessing(false);
                setProgressMessage("Course loaded from previous generation.");
                setError(""); // Clear any potential previous error
            }
             // Check if the backend started the process
            else if (result.data.video_id) {
                console.log(`Course generation started for video ID: ${result.data.video_id}`);
                // Start listening for progress updates via SSE
                listenForProgress(result.data.video_id);
                 // Progress message will be updated by SSE listener
            } else {
                 // Handle unexpected response from the initial call
                 throw new Error(result.data.message || "Invalid response from server when starting generation.");
            }

        } catch (err: any) {
            console.error("Error initiating course generation:", err);
            let errorMsg = "Failed to start course generation.";
            if (err.response) {
                // Handle specific HTTP errors from the initial POST
                errorMsg = `${err.response.data.detail || err.message} (Status: ${err.response.status})`;
            } else if (err.request) {
                errorMsg = "No response received from server. Please check connection.";
            } else {
                errorMsg = err.message || errorMsg;
            }
            setError(errorMsg);
            setIsProcessing(false);
            setProgressMessage(null);
        }
        // No finally block needed to set isProcessing(false) here,
        // as it's handled by SSE events or the direct 'already exists' case.
    };


    const handleQuizAnswer = (questionKey: string, answer: string) => {
        setSelectedAnswers(prev => ({
            ...prev,
            [questionKey]: answer
        }));
    };

    const isAnswerCorrect = (questionKey: string, question: QuestionItem) => {
        return selectedAnswers[questionKey] === question.correct_answer;
    };

    // Helper to determine if quiz button should be success/error/primary
    const getQuizButtonColor = (questionKey: string, question: QuestionItem, option: string): "success" | "error" | "primary" => {
        const selectedAnswer = selectedAnswers[questionKey];
        const isAnswered = selectedAnswer !== undefined;
        if (!isAnswered) return 'primary';
        if (selectedAnswer !== option) return 'primary'; // Only color the selected button
        return isAnswerCorrect(questionKey, question) ? 'success' : 'error';
    };

    const getQuizButtonVariant = (questionKey: string, option: string): "contained" | "outlined" => {
         const selectedAnswer = selectedAnswers[questionKey];
         return selectedAnswer === option ? "contained" : "outlined";
    };


    return (
        <ThemeProvider theme={theme}>
            <Box sx={{ maxWidth: 1200, margin: "0 auto", p: 3 }}>
                {/* --- Header --- */}
                <Typography variant="h3" gutterBottom align="center" sx={{ fontWeight: "bold", color: (theme) => theme.palette.primary.main }}>
                    Video Course Generator
                </Typography>
                 <style>{`
                    table {
                        border-collapse: collapse;
                        margin: 1rem 0;
                        width: 100%;
                    }
                    td, th {
                        border: 1px solid #ddd;
                        padding: 8px;
                        text-align: left;
                    }
                    th {
                        background-color: #f5f5f5;
                        font-weight: 600;
                    }
                    strong {
                        font-weight: 600;
                    }
                `}</style>

                {/* --- Input Section --- */}
                <Box sx={{ display: "flex", gap: 2, mb: 2 }}> {/* Reduced margin bottom */}
                    <input
                        type="text"
                        placeholder="Enter YouTube Video URL"
                        value={videoUrl}
                        onChange={(e) => setVideoUrl(e.target.value)}
                        disabled={isProcessing} // Disable input while processing
                        style={{ flex: 1, padding: "12px", borderRadius: "8px", border: "1px solid #ddd", fontSize: "16px" }}
                    />
                    <Button
                        variant="contained"
                        onClick={handleSubmit}
                        disabled={isProcessing || !videoUrl} // Disable button while processing or if URL is empty
                        sx={{ px: 4, py: 1.5, borderRadius: "8px", minWidth: '180px' }} // Ensure minimum width
                    >
                        {isProcessing ? "Generating..." : "Generate Course"}
                    </Button>
                </Box>

                 {/* --- Progress Bar and Message --- */}
                 {isProcessing && (
                     <Box sx={{ width: '100%', mb: 2 }}> {/* Added margin bottom */}
                         <LinearProgress sx={{ mb: 1 }} />
                         <Typography variant="body2" color="text.secondary" align="center">
                             {progressMessage || "Processing..."}
                         </Typography>
                     </Box>
                 )}

                 {/* --- Error Snackbar --- */}
                 {error && (
                     <Snackbar open={!!error} autoHideDuration={8000} onClose={() => setError("")} anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}>
                         <Alert severity="error" onClose={() => setError("")} sx={{ width: '100%' }}>{error}</Alert>
                     </Snackbar>
                 )}

                {/* --- Embedded Video --- */}
                {embeddedVideoUrl && !isProcessing && !response && ( // Only show if not processing and no response yet
                    <Box sx={{ mt: 2, mb: 4 }}>
                        <Typography variant="h6">Video Preview</Typography>
                        <iframe
                            ref={playerRef}
                            width="100%"
                            height="500"
                            src={`${embeddedVideoUrl}`}
                            title="YouTube video player"
                            frameBorder="0"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                            allowFullScreen
                        ></iframe>
                    </Box>
                )}


                {/* --- Results Section (Tabs) --- */}
                {/* Only render tabs and content if processing is NOT active AND response has data */}
                {!isProcessing && response?.course && (
                    <Box sx={{ mt: 4 }}>
                         {embeddedVideoUrl && ( // Show video above tabs when results are ready
                            <Box sx={{ mb: 4 }}>
                                <Typography variant="h6">Video Reference</Typography>
                                <iframe
                                    ref={playerRef}
                                    width="100%"
                                    height="500"
                                    src={`${embeddedVideoUrl}`}
                                    title="YouTube video player"
                                    frameBorder="0"
                                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                                    allowFullScreen
                                ></iframe>
                            </Box>
                        )}
                        <Tabs value={activeTab} onChange={(_, newValue) => setActiveTab(newValue)} centered>
                            <Tab label="Course Content" sx={{ textTransform: 'none' }} />
                            <Tab label="Knowledge Check" sx={{ textTransform: 'none' }} />
                            <Tab label="Retention & Review" sx={{ textTransform: 'none' }} />
                        </Tabs>

                        {/* --- Tab Content --- */}

                        {/* Course Content Tab (Tab 0) */}
                        {activeTab === 0 && response.course?.course_content?.modules && (
                            <Box sx={{ mt: 3 }}>
                                {/* ... Keep your existing Course Content rendering logic ... */}
                                <Typography variant="h5" gutterBottom>Course Content</Typography>
                                {response.course.course_content.modules.map((module, moduleIndex) => (
                                    <Accordion key={`content-mod-${moduleIndex}`} defaultExpanded={moduleIndex === 0} elevation={1} square sx={{ mb: 1 }}>
                                        <AccordionSummary expandIcon={<ExpandMoreIcon />} >
                                            <Typography variant="h6" sx={{ fontWeight: 'bold' }}>{module.module_title}</Typography>
                                        </AccordionSummary>
                                        <AccordionDetails sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                                            {module.sections.map((section, sectionIndex) => (
                                                <Box key={`content-sec-${moduleIndex}-${sectionIndex}`} sx={{ borderLeft: '3px solid', borderColor: theme.palette.primary.light, pl: 2 }}>
                                                    <Typography variant="subtitle1" sx={{ fontWeight: 'bold', mb: 1 }}>{section.section_title}</Typography>
                                                    {/* Ensure content exists before rendering */}
                                                    {section.content && <ReactMarkdown remarkPlugins={[remarkGfm]} children={section.content} />}

                                                    {section.media && section.media.length > 0 && (
                                                        <Box sx={{ mt: 2 }}>
                                                            <Typography variant="subtitle2" sx={{ fontWeight: "bold", mb: 1 }}>Visual Aids:</Typography>
                                                            <Grid container spacing={2}>
                                                                {section.media.map((mediaItem, mediaIndex) => (
                                                                    <Grid item xs={12} sm={6} md={4} key={`content-media-${moduleIndex}-${sectionIndex}-${mediaIndex}`}>
                                                                        <Card elevation={0} sx={{ border: '1px solid #eee', height: '100%' }}>
                                                                            {mediaItem.frame_path && mediaItem.frame_path.startsWith('http') ? ( // Check if it's a URL
                                                                                <img
                                                                                    src={mediaItem.frame_path}
                                                                                    alt={mediaItem.caption || `Frame for ${section.section_title}`}
                                                                                    style={{ width: "100%", height: "auto", display: 'block', aspectRatio: '16/9', objectFit: 'cover' }} // Added aspect ratio
                                                                                    onError={(e) => { e.currentTarget.style.display = 'none'; console.error("Failed to load image:", mediaItem.frame_path); } } // Handle image load errors
                                                                                />
                                                                            ) : (
                                                                                <Box sx={{ height: 150, display: 'flex', alignItems: 'center', justifyContent: 'center', bgcolor: '#f0f0f0' }}>
                                                                                    <Typography variant="caption" color="textSecondary">Image not available</Typography>
                                                                                </Box>
                                                                            )}
                                                                            <CardContent sx={{ pt: 1 }}>
                                                                                 {/* Ensure caption exists */}
                                                                                 {mediaItem.caption && (
                                                                                     <ReactMarkdown
                                                                                        remarkPlugins={[remarkGfm]}
                                                                                        components={{
                                                                                            p: ({ children }) => <Typography component="p" variant="caption" display="block" sx={{ mt: 0.5 }}>{children}</Typography>,
                                                                                        }}
                                                                                        children={mediaItem.caption}
                                                                                    />
                                                                                 )}
                                                                                 {/* Ensure timestamp is valid */}
                                                                                 {typeof mediaItem.timestamp === 'number' && !isNaN(mediaItem.timestamp) && (
                                                                                    <Button size="small" variant='text' color="primary" sx={{ textTransform: 'none', p: 0, mt: 0.5 }}>
                                                                                        Timestamp: {formatTimestamp(mediaItem.timestamp)}
                                                                                    </Button>
                                                                                 )}
                                                                            </CardContent>
                                                                        </Card>
                                                                    </Grid>
                                                                ))}
                                                            </Grid>
                                                        </Box>
                                                    )}
                                                </Box>
                                            ))}
                                        </AccordionDetails>
                                    </Accordion>
                                ))}
                            </Box>
                        )}

                        {/* Quiz Tab (Tab 1) */}
                        {activeTab === 1 && response.course?.quiz_content?.quizzes && (
                            <Box sx={{ mt: 3 }}>
                                <Typography variant="h5" gutterBottom sx={{ fontWeight: 'bold', textAlign: 'left', mb: 4, color: 'text.primary' }}>
                                    Knowledge Check
                                </Typography>
                                {response.course.quiz_content.quizzes.map((quiz, quizIndex) => (
                                    <Accordion key={`quiz-acc-${quizIndex}`} elevation={1} square sx={{ mb: 1 }}>
                                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                                            <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                                                {quiz.module_title} - {quiz.section_title}
                                            </Typography>
                                        </AccordionSummary>
                                        <AccordionDetails sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                                            {quiz.questions.map((question, questionIndex) => {
                                                const questionKey = `${quiz.module_title}-${quizIndex}-${questionIndex}`;
                                                const selectedAnswer = selectedAnswers[questionKey];
                                                const isAnswered = selectedAnswer !== undefined;
                                                const isCorrect = isAnswered && isAnswerCorrect(questionKey, question);

                                                return (
                                                    <Card key={questionKey} elevation={0} sx={{ border: '1px solid #eee', borderRadius: '8px' }}>
                                                        <CardContent>
                                                            <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 'bold', color: 'text.secondary', mb: 1 }}>
                                                                Question {questionIndex + 1}
                                                            </Typography>
                                                            <Typography variant="body1" gutterBottom sx={{ fontWeight: 500, color: 'text.primary', mb: 2, lineHeight: 1.6 }}>
                                                                {question.text}
                                                            </Typography>

                                                            {/* Options */}
                                                            {question.type === 'multiple_choice' && question.options && (
                                                                <Grid container spacing={1.5}>
                                                                    {question.options.map((option, optIndex) => (
                                                                        <Grid item xs={12} sm={6} key={optIndex}>
                                                                            <Button
                                                                                fullWidth
                                                                                variant={getQuizButtonVariant(questionKey, option)}
                                                                                color={getQuizButtonColor(questionKey, question, option)}
                                                                                onClick={() => !isAnswered && handleQuizAnswer(questionKey, option)} // Prevent changing answer
                                                                                disabled={isAnswered && selectedAnswer !== option} // Disable other options after answering
                                                                                sx={{
                                                                                    textTransform: 'none',
                                                                                    justifyContent: 'flex-start',
                                                                                    textAlign: 'left',
                                                                                    padding: '10px 16px',
                                                                                    borderRadius: '8px',
                                                                                    fontWeight: 400,
                                                                                    // Add subtle transition
                                                                                    transition: 'background-color 0.2s ease, border-color 0.2s ease',
                                                                                }}
                                                                            >
                                                                                <span style={{ marginRight: '10px', fontWeight: 'bold' }}>{String.fromCharCode(65 + optIndex)}.</span>
                                                                                {option}
                                                                            </Button>
                                                                        </Grid>
                                                                    ))}
                                                                </Grid>
                                                            )}

                                                            {question.type === 'true_false' && (
                                                                <Grid container spacing={2}>
                                                                    {["true", "false"].map((option) => (
                                                                        <Grid item xs={6} key={option}>
                                                                            <Button
                                                                                fullWidth
                                                                                variant="outlined"
                                                                                color={isAnswered ? (selectedAnswer === option ? (isCorrect ? 'success' : 'error') : 'primary') : 'primary'}
                                                                                onClick={() => handleQuizAnswer(questionKey, option)}
                                                                                sx={{
                                                                                    textTransform: 'none',
                                                                                    justifyContent: 'center',
                                                                                    padding: '10px 16px',
                                                                                    borderRadius: '8px',
                                                                                    borderColor: isAnswered ? (selectedAnswer === option ? (isCorrect ? '#2ecc71' : '#e74c3c') : '#ddd') : '#ddd',

                                                                                    '&:hover': {
                                                                                        backgroundColor: isAnswered ? (selectedAnswer === option ? (isCorrect ? 'rgba(46, 204, 113, 0.1)' : 'rgba(231, 76, 60, 0.1)') : 'rgba(0, 0, 0, 0.04)') : 'rgba(0, 0, 0, 0.04)',
                                                                                        borderColor: isAnswered ? (selectedAnswer === option ? (isCorrect ? '#2ecc71' : '#e74c3c') : '#aaa') : '#aaa',
                                                                                    }
                                                                                }}
                                                                            >
                                                                                {option.charAt(0).toUpperCase() + option.slice(1)}
                                                                            </Button>
                                                                        </Grid>
                                                                    ))}
                                                                </Grid>
                                                            )}

                                                            {isAnswered && (
                                                                <Box sx={{ mt: 2.5, p: 1.5, borderRadius: '8px', border: '1px solid', borderColor: isCorrect ? theme.palette.success.main : theme.palette.error.main, backgroundColor: isCorrect ? 'rgba(46, 204, 113, 0.05)' : 'rgba(231, 76, 60, 0.05)' }}>
                                                                    <Typography sx={{ color: isCorrect ? theme.palette.success.dark : theme.palette.error.dark, fontWeight: 'bold', mb: 0.5 }}>
                                                                        {isCorrect ? 'Correct!' : 'Incorrect.'}
                                                                    </Typography>
                                                                    {(question.rationale || (!isCorrect && `Correct Answer: ${question.correct_answer}`)) && ( // Show correct answer if wrong
                                                                        <Typography variant="body2" sx={{ mt: 0.5, color: 'text.secondary' }}>
                                                                            {question.rationale || (!isCorrect ? `Correct Answer: ${question.correct_answer}` : "")}
                                                                        </Typography>
                                                                    )}
                                                                    {/* Ensure timestamp is valid before displaying */}
                                                                    {question.review_timestamp && typeof question.review_timestamp === 'string' && (
                                                                        <Typography variant="caption" sx={{ display: 'block', fontStyle: 'italic', mt: 1, color: 'text.secondary' }}>
                                                                           Review around: {question.review_timestamp}
                                                                        </Typography>
                                                                    )}
                                                                </Box>
                                                            )}
                                                        </CardContent>
                                                    </Card>
                                                );
                                            })}
                                        </AccordionDetails>
                                    </Accordion>
                                ))}
                            </Box>
                        )}

                        {/* Retention Tab (Tab 2) */}
                        {activeTab === 2 && response.course?.retention_plan?.retention_plan && (
                            <Box sx={{ mt: 3 }}>
                                <Typography variant="h5" gutterBottom sx={{ fontWeight: 'bold', textAlign: 'left', mb: 2, color: 'text.primary' }}>
                                    Retention & Review Plan
                                </Typography>
                                {response.course.retention_plan.retention_plan.overall_summary && (
                                     <Card elevation={0} sx={{ border: '1px solid #eee', borderRadius: '8px', p: 2, mb: 3, backgroundColor: '#f9f9f9' }}>
                                         <Typography variant="h6" gutterBottom sx={{ fontWeight: 'bold' }}>Overall Summary</Typography>
                                        <ReactMarkdown remarkPlugins={[remarkGfm]} children={response.course.retention_plan.retention_plan.overall_summary} />
                                    </Card>
                                )}

                                {response.course.retention_plan.retention_plan.module_retention.map((moduleRetention, moduleIndex) => (
                                    <Accordion key={`retention-mod-${moduleIndex}`} elevation={1} square sx={{ mb: 1 }}>
                                        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                                            <Typography variant="h6" sx={{ fontWeight: 'bold' }}>{moduleRetention.module_title}</Typography>
                                        </AccordionSummary>
                                        <AccordionDetails sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
                                            {moduleRetention.retention_tips && moduleRetention.retention_tips.length > 0 && (
                                                <Box>
                                                    <Typography variant="subtitle1" sx={{ fontWeight: "bold", mb: 1 }}>Retention Activities:</Typography>
                                                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                                        {moduleRetention.retention_tips.map((tip, tipIndex) => (
                                                            <Card key={`ret-tip-${moduleIndex}-${tipIndex}`} elevation={0} sx={{ border: '1px solid #eee', p: 2 }}>
                                                                <Typography variant="body1" sx={{ fontWeight: "bold", mb: 0.5 }}>Activity ({tip.type}):</Typography>
                                                                <Typography variant="body2" sx={{ fontStyle: 'italic', mb: 1 }}>{tip.description}</Typography>
                                                                {tip.executed_content && tip.executed_content !== "Error executing tip" && tip.executed_content !== "Skipped - No Description" && tip.executed_content !== "Not Executed" && !tip.executed_content.startsWith("Skipped - Unknown Type:") ? (
                                                                     <ReactMarkdown
                                                                        remarkPlugins={[remarkGfm]}
                                                                        children={tip.executed_content}
                                                                        components={{
                                                                            // Add styling for tables generated by markdown here if needed
                                                                            table: ({node, ...props}) => <table style={{width: '100%', borderCollapse: 'collapse', marginTop: '1em'}} {...props} />,
                                                                            th: ({node, ...props}) => <th style={{border: '1px solid #ddd', padding: '8px', backgroundColor: '#f2f2f2', textAlign: 'left'}} {...props} />,
                                                                            td: ({node, ...props}) => <td style={{border: '1px solid #ddd', padding: '8px', textAlign: 'left'}} {...props} />,
                                                                        }}
                                                                    />
                                                                ) : (
                                                                    <Typography variant='caption' color="textSecondary">(Activity content not generated or failed)</Typography>
                                                                )}

                                                            </Card>
                                                        ))}
                                                    </Box>
                                                </Box>
                                            )}

                                            {moduleRetention.spaced_repetition_prompts && moduleRetention.spaced_repetition_prompts.length > 0 && (
                                                <Box>
                                                    <Typography variant="subtitle1" sx={{ fontWeight: "bold", mb: 1 }}>Recall Prompts:</Typography>
                                                    <ul style={{ paddingLeft: '20px', margin: 0 }}>
                                                        {moduleRetention.spaced_repetition_prompts.map((prompt, promptIndex) => (
                                                            <li key={`ret-prompt-${moduleIndex}-${promptIndex}`} style={{ marginBottom: '8px' }}>
                                                                <Typography variant="body2">{prompt}</Typography>
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </Box>
                                            )}

                                            {moduleRetention.scenario_examples && moduleRetention.scenario_examples.length > 0 && (
                                                 <Box>
                                                    <Typography variant="subtitle1" sx={{ fontWeight: "bold", mb: 1 }}>Application Scenarios:</Typography>
                                                     <ul style={{ paddingLeft: '20px', margin: 0 }}>
                                                        {moduleRetention.scenario_examples.map((example, exampleIndex) => (
                                                             <li key={`ret-scenario-${moduleIndex}-${exampleIndex}`} style={{ marginBottom: '8px' }}>
                                                                <Typography variant="body2">{example}</Typography>
                                                             </li>
                                                        ))}
                                                    </ul>
                                                </Box>
                                            )}
                                        </AccordionDetails>
                                    </Accordion>
                                ))}

                            </Box>
                        )}

                    </Box> /* End Tabs Container */
                )}
            </Box> {/* End Main Container */}
        </ThemeProvider>
    );
};

export default UploadPage;